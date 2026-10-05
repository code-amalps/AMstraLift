"""Client for the Google Open Source Vulnerabilities (OSV.dev) API.

Provides free, fast vulnerability lookups across npm, NuGet, and PyPI.
Docs: https://google.github.io/osv.dev/post-v1-query/
"""

import logging
import re
from typing import Any, Callable

import httpx

logger = logging.getLogger("amstralift.security.osv_client")

from amstralift.governance.vulnerabilities import VulnerabilitySeverity
from amstralift.security.models import AuditReport, VulnerabilityFinding

OSV_QUERY_URL = "https://api.osv.dev/v1/query"
OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"

ECOSYSTEM_MAP = {
    "angular": "npm",
    "react": "npm",
    "npm": "npm",
    "dotnet": "NuGet",
    "nuget": "NuGet",
    "python": "PyPI",
    "pypi": "PyPI",
}

SEVERITY_RANK: dict[VulnerabilitySeverity, int] = {
    VulnerabilitySeverity.CRITICAL: 4,
    VulnerabilitySeverity.HIGH: 3,
    VulnerabilitySeverity.MEDIUM: 2,
    VulnerabilitySeverity.LOW: 1,
}


def parse_severity(raw_vuln: dict[str, Any]) -> tuple[VulnerabilitySeverity, float | None]:
    """Parse severity and CVSS score from OSV vulnerability record."""
    # 1. Check database_specific severity (GHSA/NVD)
    db_spec = raw_vuln.get("database_specific", {})
    raw_sev = str(db_spec.get("severity", "")).upper()
    if raw_sev in ("CRITICAL", "HIGH", "MODERATE", "MEDIUM", "LOW"):
        if raw_sev == "MODERATE":
            return VulnerabilitySeverity.MEDIUM, None
        return VulnerabilitySeverity(raw_sev), None

    # 2. Check ecosystem_specific
    eco_spec = raw_vuln.get("ecosystem_specific", {})
    raw_sev = str(eco_spec.get("severity", "")).upper()
    if raw_sev in ("CRITICAL", "HIGH", "MODERATE", "MEDIUM", "LOW"):
        if raw_sev == "MODERATE":
            return VulnerabilitySeverity.MEDIUM, None
        return VulnerabilitySeverity(raw_sev), None

    # 3. Check severity array (CVSS v3)
    cvss_score = None
    for s in raw_vuln.get("severity", []):
        score_val = s.get("score")
        if isinstance(score_val, str) and score_val.startswith("CVSS:"):
            # Estimate from vector or default to medium
            cvss_score = 6.0
        elif isinstance(score_val, (int, float)):
            cvss_score = float(score_val)

    if cvss_score is not None:
        if cvss_score >= 9.0:
            return VulnerabilitySeverity.CRITICAL, cvss_score
        if cvss_score >= 7.0:
            return VulnerabilitySeverity.HIGH, cvss_score
        if cvss_score >= 4.0:
            return VulnerabilitySeverity.MEDIUM, cvss_score
        return VulnerabilitySeverity.LOW, cvss_score

    return VulnerabilitySeverity.MEDIUM, None


def _parse_semver_tuple(ver_str: str) -> tuple[int, ...]:
    """Parse semver string to integer tuple for comparison."""
    clean = ver_str.strip().lstrip("^~>=<v")
    clean = clean.split("-")[0].split("+")[0]
    parts = []
    for p in clean.split("."):
        if p.isdigit():
            parts.append(int(p))
        else:
            match = re.match(r"^(\d+)", p)
            parts.append(int(match.group(1)) if match else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def select_minimal_fixed_version(current_ver: str, fixed_versions: list[str]) -> str | None:
    """Select the minimal secure fixed version that solves the vulnerability.

    Prefers patch/minor upgrades in the same major branch over higher majors.
    """
    if not fixed_versions:
        return None

    cur_tuple = _parse_semver_tuple(current_ver)
    valid_fixes = []

    for fv in fixed_versions:
        f_tuple = _parse_semver_tuple(fv)
        if f_tuple >= cur_tuple:
            valid_fixes.append((f_tuple, fv))

    if not valid_fixes:
        # If all fixed versions look lower or different, return the highest fixed
        sorted_fixes = sorted([(_parse_semver_tuple(fv), fv) for fv in fixed_versions], key=lambda x: x[0])
        return sorted_fixes[-1][1] if sorted_fixes else None

    # Sort ascending by version tuple
    valid_fixes.sort(key=lambda x: x[0])

    # 1. Prefer fix in the exact same major
    same_major = [item for item in valid_fixes if item[0][0] == cur_tuple[0]]
    if same_major:
        return same_major[0][1]

    # 2. Otherwise lowest version >= current
    return valid_fixes[0][1]


class OSVClient:
    """Client for scanning dependencies using OSV.dev API."""

    def __init__(self, timeout_seconds: float = 10.0, max_scan_seconds: float = 60.0):
        self.timeout_seconds = timeout_seconds
        self.max_scan_seconds = max_scan_seconds
        self._vuln_cache: dict[str, dict[str, Any]] = {}
        self._dep_query_cache: dict[tuple[str, str, str], list[dict[str, Any]]] = {}

    def map_ecosystem(self, ecosystem: str) -> str:
        eco_clean = ecosystem.lower().strip()
        if eco_clean in ECOSYSTEM_MAP:
            return ECOSYSTEM_MAP[eco_clean]
        raise ValueError(f"Unsupported ecosystem for OSV scan: {ecosystem}. Supported: {list(ECOSYSTEM_MAP.keys())}")

    def query_package(
        self,
        package_name: str,
        version: str,
        ecosystem: str,
    ) -> list[VulnerabilityFinding]:
        """Query OSV for vulnerabilities affecting a single package and version."""
        osv_eco = self.map_ecosystem(ecosystem)
        clean_ver = version.lstrip("^~>=<v").strip()
        payload = {
            "package": {"name": package_name, "ecosystem": osv_eco},
            "version": clean_ver,
        }

        findings: list[VulnerabilityFinding] = []
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                res = client.post(OSV_QUERY_URL, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    vulns = data.get("vulns", [])
                    for v in vulns:
                        finding = self._build_finding(v, package_name, version, osv_eco)
                        if finding:
                            findings.append(finding)
        except Exception:
            return []

        return findings

    def scan_dependencies(
        self,
        dependencies: dict[str, str],
        ecosystem: str,
        repo_path: str = "",
    ) -> AuditReport:
        """Scan a dictionary of {package_name: version} dependencies."""
        osv_eco = self.map_ecosystem(ecosystem)
        findings: list[VulnerabilityFinding] = []

        if not dependencies:
            return AuditReport(
                repo_path=repo_path,
                ecosystem=ecosystem,
                scanned_packages_count=0,
                findings=[],
            )

        # Filter valid items and clean versions
        items = [
            (pkg, ver.lstrip("^~>=<v").strip())
            for pkg, ver in dependencies.items()
            if ver and ver != "*"
        ]
        chunk_size = 100

        with httpx.Client(timeout=self.timeout_seconds * 1.5) as client:
            for i in range(0, len(items), chunk_size):
                chunk = items[i : i + chunk_size]
                queries = [
                    {"package": {"name": pkg, "ecosystem": osv_eco}, "version": ver}
                    for pkg, ver in chunk
                ]

                try:
                    res = client.post(OSV_BATCH_URL, json={"queries": queries})
                    if res.status_code == 200:
                        batch_res = res.json().get("results", [])
                        for idx, item in enumerate(batch_res):
                            if idx < len(chunk):
                                vulns = item.get("vulns", [])
                                pkg_name, cur_ver = chunk[idx]
                                self._dep_query_cache[(pkg_name, cur_ver, osv_eco)] = vulns
                                for v in vulns:
                                    finding = self._build_finding(v, pkg_name, cur_ver, osv_eco, client=client)
                                    if finding:
                                        findings.append(finding)
                except Exception as exc:
                    logger.debug("OSV batch query failed for chunk: %s", exc)

        findings.sort(key=lambda f: SEVERITY_RANK.get(f.severity, 0), reverse=True)
        return AuditReport(
            repo_path=repo_path,
            ecosystem=ecosystem,
            scanned_packages_count=len(dependencies),
            findings=findings,
        )

    def get_vuln_details(self, vuln_id: str, client: httpx.Client | None = None) -> dict[str, Any] | None:
        """Fetch full details for an individual vulnerability by ID with in-memory cache."""
        if not hasattr(self, "_vuln_cache"):
            self._vuln_cache: dict[str, dict[str, Any]] = {}
        if vuln_id in self._vuln_cache:
            return self._vuln_cache[vuln_id]

        url = f"https://api.osv.dev/v1/vulns/{vuln_id}"
        try:
            if client:
                res = client.get(url)
            else:
                with httpx.Client(timeout=self.timeout_seconds) as c:
                    res = c.get(url)
            if res.status_code == 200:
                data = res.json()
                self._vuln_cache[vuln_id] = data
                return data
        except Exception:
            return None
        return None

    def _build_finding(
        self,
        raw_vuln: dict[str, Any],
        package_name: str,
        current_version: str,
        ecosystem: str,
        client: httpx.Client | None = None,
    ) -> VulnerabilityFinding | None:
        """Parse an OSV vuln dictionary into a VulnerabilityFinding."""
        vuln_id = raw_vuln.get("id", "UNKNOWN")
        # If raw_vuln is from querybatch (lightweight), fetch full details
        if "affected" not in raw_vuln and vuln_id != "UNKNOWN":
            full = self.get_vuln_details(vuln_id, client=client)
            if full:
                raw_vuln = full

        cve_id = raw_vuln.get("id", vuln_id)
        # Check aliases for CVE ID (e.g. if id is GHSA, but has CVE alias)
        aliases = raw_vuln.get("aliases", [])
        for a in aliases:
            if a.startswith("CVE-"):
                cve_id = f"{cve_id} ({a})"
                break

        severity, cvss = parse_severity(raw_vuln)
        summary = raw_vuln.get("summary") or raw_vuln.get("details", "")[:120].strip()

        # Extract fixed versions from ranges
        fixed_versions: list[str] = []
        for aff in raw_vuln.get("affected", []):
            if aff.get("package", {}).get("name", "").lower() == package_name.lower():
                for rg in aff.get("ranges", []):
                    for ev in rg.get("events", []):
                        if "fixed" in ev:
                            fixed_versions.append(ev["fixed"])

        fixed_ver = select_minimal_fixed_version(current_version, fixed_versions)

        return VulnerabilityFinding(
            cve_id=cve_id,
            package_name=package_name,
            ecosystem=ecosystem,
            current_version=current_version,
            severity=severity,
            fixed_version=fixed_ver,
            all_fixed_versions=fixed_versions,
            summary=summary,
            details=raw_vuln.get("details", ""),
            cvss_score=cvss,
        )

    def scan_discovered_dependencies(
        self,
        dependencies: list[Any],  # list[DiscoveredDependency]
        ecosystem: str,
        repo_path: str = "",
        governance_manager: Any = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> AuditReport:
        """Scan a list of DiscoveredDependency objects, preserving direct/transitive relationships."""
        import time

        osv_eco = self.map_ecosystem(ecosystem)
        findings: list[VulnerabilityFinding] = []

        if not dependencies:
            return AuditReport(
                repo_path=repo_path,
                ecosystem=ecosystem,
                scanned_packages_count=0,
                findings=[],
            )

        start_t = time.time()

        # 1. Group dependencies by (package_name, clean_ver) to deduplicate queries
        dep_map: dict[tuple[str, str], list[Any]] = {}
        for dep in dependencies:
            clean_ver = dep.version.lstrip("^~>=<v").strip() if dep.version and dep.version != "*" else ""
            dep_map.setdefault((dep.package_name, clean_ver), []).append(dep)

        # 2. Separate cached vs uncached unique keys
        uncached_keys: list[tuple[str, str]] = []
        vulns_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}

        for (pkg_name, clean_ver) in dep_map.keys():
            if not clean_ver:
                continue
            cache_key = (pkg_name, clean_ver, osv_eco)
            if cache_key in self._dep_query_cache:
                vulns_by_key[(pkg_name, clean_ver)] = self._dep_query_cache[cache_key]
            else:
                uncached_keys.append((pkg_name, clean_ver))

        # 3. Query uncached unique keys in chunks
        chunk_size = 100
        total_chunks = (len(uncached_keys) + chunk_size - 1) // chunk_size if uncached_keys else 0

        with httpx.Client(timeout=self.timeout_seconds * 1.5) as client:
            for chunk_idx, i in enumerate(range(0, len(uncached_keys), chunk_size)):
                if time.time() - start_t > self.max_scan_seconds:
                    timeout_msg = f"↳ [Warning] OSV database scan exceeded {int(self.max_scan_seconds)}s time budget; proceeding with partial results."
                    if progress_callback:
                        progress_callback(timeout_msg)
                    else:
                        print(f"   {timeout_msg}", flush=True)
                    break

                chunk_keys = uncached_keys[i : i + chunk_size]
                progress_msg = f"↳ Querying OSV database: Batch {chunk_idx + 1}/{total_chunks} ({len(chunk_keys)} unique packages)..."
                if progress_callback:
                    progress_callback(progress_msg)
                else:
                    print(f"   {progress_msg}", flush=True)

                queries = [
                    {"package": {"name": pkg, "ecosystem": osv_eco}, "version": ver}
                    for pkg, ver in chunk_keys
                ]

                def _post_batch(batch_queries: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
                    try:
                        res = client.post(OSV_BATCH_URL, json={"queries": batch_queries})
                        if res.status_code == 200:
                            return res.json().get("results", [])
                    except Exception:
                        pass
                    return None

                batch_results = _post_batch(queries)
                if batch_results is None and len(queries) > 25:
                    # Retry in two smaller sub-batches
                    mid = len(queries) // 2
                    sub1 = _post_batch(queries[:mid])
                    sub2 = _post_batch(queries[mid:])
                    if sub1 is not None and sub2 is not None:
                        batch_results = sub1 + sub2

                if batch_results is not None:
                    for idx, item in enumerate(batch_results):
                        if idx < len(chunk_keys):
                            k = chunk_keys[idx]
                            v_list = item.get("vulns", [])
                            vulns_by_key[k] = v_list
                            self._dep_query_cache[(k[0], k[1], osv_eco)] = v_list
                else:
                    logger.warning("OSV database query timed out or failed for batch of %d packages; skipping batch.", len(chunk_keys))
                    for k in chunk_keys:
                        vulns_by_key[k] = []
                        self._dep_query_cache[(k[0], k[1], osv_eco)] = []

            # 4. Build findings for each matched vulnerability and associate with every dependent
            for (pkg_name, clean_ver), vulns in vulns_by_key.items():
                if not vulns:
                    continue
                deps_for_key = dep_map.get((pkg_name, clean_ver), [])
                sample_ver = deps_for_key[0].version if deps_for_key else clean_ver
                for v in vulns:
                    base_finding = self._build_finding(v, pkg_name, sample_ver, osv_eco, client=client)
                    if not base_finding:
                        continue
                    for dep in deps_for_key:
                        finding = base_finding.model_copy(deep=True)
                        finding.is_direct = dep.is_direct
                        finding.introduced_by = list(dep.introduced_by)
                        if governance_manager:
                            cov = governance_manager.check_coverage(
                                finding.cve_id, finding.package_name, finding.current_version
                            )
                            if cov:
                                finding.is_exempted = True
                                finding.exemption_id = cov.exception_id
                        findings.append(finding)

        scan_duration = time.time() - start_t
        findings.sort(key=lambda f: SEVERITY_RANK.get(f.severity, 0), reverse=True)
        summary_msg = f"↳ OSV scan completed: {len(findings)} advisories detected across {len(dependencies)} packages ({scan_duration:.1f}s)."
        if progress_callback:
            progress_callback(summary_msg)
        else:
            print(f"   {summary_msg}", flush=True)

        return AuditReport(
            repo_path=repo_path,
            ecosystem=ecosystem,
            scanned_packages_count=len(dependencies),
            findings=findings,
        )
