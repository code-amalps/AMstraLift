"""Client for the Google Open Source Vulnerabilities (OSV.dev) API.

Provides free, fast vulnerability lookups across npm, NuGet, and PyPI.
Docs: https://google.github.io/osv.dev/post-v1-query/
"""

import re
from typing import Any

import httpx

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

    def __init__(self, timeout_seconds: float = 10.0):
        self.timeout_seconds = timeout_seconds

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

        # Batch query in chunks of 100
        items = list(dependencies.items())
        chunk_size = 100

        for i in range(0, len(items), chunk_size):
            chunk = items[i : i + chunk_size]
            queries = [
                {
                    "package": {"name": pkg, "ecosystem": osv_eco},
                    "version": ver.lstrip("^~>=<v").strip(),
                }
                for pkg, ver in chunk
                if ver and ver != "*"
            ]

            if not queries:
                continue

            try:
                with httpx.Client(timeout=self.timeout_seconds * 2) as client:
                    res = client.post(OSV_BATCH_URL, json={"queries": queries})
                    if res.status_code == 200:
                        batch_res = res.json().get("results", [])
                        for idx, item in enumerate(batch_res):
                            vulns = item.get("vulns", [])
                            pkg_name, cur_ver = chunk[idx]
                            for v in vulns:
                                finding = self._build_finding(v, pkg_name, cur_ver, osv_eco)
                                if finding:
                                    findings.append(finding)
            except Exception:
                # If batch query fails, fall back to individual queries for this chunk
                for pkg, ver in chunk:
                    findings.extend(self.query_package(pkg, ver, ecosystem))

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
    ) -> AuditReport:
        """Scan a list of DiscoveredDependency objects, preserving direct/transitive relationships."""
        osv_eco = self.map_ecosystem(ecosystem)
        findings: list[VulnerabilityFinding] = []

        if not dependencies:
            return AuditReport(
                repo_path=repo_path,
                ecosystem=ecosystem,
                scanned_packages_count=0,
                findings=[],
            )

        # Batch query in chunks of 100
        chunk_size = 100
        for i in range(0, len(dependencies), chunk_size):
            chunk = dependencies[i : i + chunk_size]
            queries = [
                {
                    "package": {"name": dep.package_name, "ecosystem": osv_eco},
                    "version": dep.version.lstrip("^~>=<v").strip(),
                }
                for dep in chunk
                if dep.version and dep.version != "*"
            ]

            if not queries:
                continue

            try:
                with httpx.Client(timeout=self.timeout_seconds * 2) as client:
                    res = client.post(OSV_BATCH_URL, json={"queries": queries})
                    if res.status_code == 200:
                        batch_res = res.json().get("results", [])
                        for idx, item in enumerate(batch_res):
                            vulns = item.get("vulns", [])
                            dep = chunk[idx]
                            for v in vulns:
                                finding = self._build_finding(v, dep.package_name, dep.version, osv_eco, client=client)
                                if finding:
                                    finding.is_direct = dep.is_direct
                                    finding.introduced_by = dep.introduced_by
                                    if governance_manager:
                                        cov = governance_manager.check_coverage(
                                            finding.cve_id, finding.package_name, finding.current_version
                                        )
                                        if cov:
                                            finding.is_exempted = True
                                            finding.exemption_id = cov.exception_id
                                    findings.append(finding)
            except Exception:
                for dep in chunk:
                    sub_findings = self.query_package(dep.package_name, dep.version, ecosystem)
                    for sf in sub_findings:
                        sf.is_direct = dep.is_direct
                        sf.introduced_by = dep.introduced_by
                        if governance_manager:
                            cov = governance_manager.check_coverage(sf.cve_id, sf.package_name, sf.current_version)
                            if cov:
                                sf.is_exempted = True
                                sf.exemption_id = cov.exception_id
                    findings.extend(sub_findings)

        return AuditReport(
            repo_path=repo_path,
            ecosystem=ecosystem,
            scanned_packages_count=len(dependencies),
            findings=findings,
        )
