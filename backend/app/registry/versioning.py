"""版本管理：语义化版本、自动递增、版本对比"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class SemanticVersion:
    """语义化版本"""
    major: int
    minor: int
    patch: int
    prerelease: str = ""
    build: str = ""
    
    def __str__(self) -> str:
        version = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            version += f"-{self.prerelease}"
        if self.build:
            version += f"+{self.build}"
        return version
    
    @classmethod
    def parse(cls, version: str) -> "SemanticVersion":
        """解析版本字符串"""
        # 正则匹配语义化版本
        pattern = r"^(\d+)\.(\d+)\.(\d+)(?:-([a-zA-Z0-9.-]+))?(?:\+([a-zA-Z0-9.-]+))?$"
        match = re.match(pattern, version.strip())
        if not match:
            # 尝试简单版本号
            simple_pattern = r"^(\d+)\.(\d+)\.(\d+)$"
            simple_match = re.match(simple_pattern, version.strip())
            if simple_match:
                return cls(int(simple_match.group(1)), int(simple_match.group(2)), int(simple_match.group(3)))
            raise ValueError(f"无效的版本格式: {version}")
        
        return cls(
            major=int(match.group(1)),
            minor=int(match.group(2)),
            patch=int(match.group(3)),
            prerelease=match.group(4) or "",
            build=match.group(5) or "",
        )
    
    def increment_major(self) -> "SemanticVersion":
        return SemanticVersion(self.major + 1, 0, 0)
    
    def increment_minor(self) -> "SemanticVersion":
        return SemanticVersion(self.major, self.minor + 1, 0)
    
    def increment_patch(self) -> "SemanticVersion":
        return SemanticVersion(self.major, self.minor, self.patch + 1)
    
    def with_prerelease(self, prerelease: str) -> "SemanticVersion":
        return SemanticVersion(self.major, self.minor, self.patch, prerelease, self.build)
    
    def with_build(self, build: str) -> "SemanticVersion":
        return SemanticVersion(self.major, self.minor, self.patch, self.prerelease, build)
    
    def __lt__(self, other: "SemanticVersion") -> bool:
        return (self.major, self.minor, self.patch) < (other.major, other.minor, other.patch)
    
    def __le__(self, other: "SemanticVersion") -> bool:
        return self == other or self < other
    
    def __gt__(self, other: "SemanticVersion") -> bool:
        return (self.major, self.minor, self.patch) > (other.major, other.minor, other.patch)
    
    def __ge__(self, other: "SemanticVersion") -> bool:
        return self == other or self > other
    
    def __eq__(self, other: "SemanticVersion") -> bool:
        return (self.major, self.minor, self.patch, self.prerelease) == (other.major, other.minor, other.patch, other.prerelease)


class VersionManager:
    """版本管理器"""
    
    def __init__(self, scheme: str = "semver"):
        self.scheme = scheme  # semver/auto_increment/timestamp
    
    def generate_next_version(
        self, 
        current_versions: List[str], 
        bump: str = "patch"
    ) -> str:
        """生成下一个版本号"""
        if self.scheme == "timestamp":
            import time
            return time.strftime("%Y%m%d-%H%M%S")
        
        if self.scheme == "auto_increment":
            if not current_versions:
                return "1.0.0"
            # 简单取最大版本并递增 patch
            latest = max(current_versions, key=self._version_key)
            return self._increment_simple(latest)
        
        # semver
        if not current_versions:
            return "1.0.0"
        
        # 解析所有版本，取最大
        parsed = []
        for v in current_versions:
            try:
                parsed.append(SemanticVersion.parse(v))
            except ValueError:
                continue
        
        if not parsed:
            return "1.0.0"
        
        latest = max(parsed)
        
        if bump == "major":
            return str(latest.increment_major())
        elif bump == "minor":
            return str(latest.increment_minor())
        else:  # patch
            return str(latest.increment_patch())
    
    def _version_key(self, version: str) -> Tuple:
        """版本排序键"""
        try:
            sv = SemanticVersion.parse(version)
            return (sv.major, sv.minor, sv.patch)
        except ValueError:
            return (0, 0, 0)
    
    def _increment_simple(self, version: str) -> str:
        """简单版本递增"""
        try:
            parts = version.split(".")
            if len(parts) == 3:
                major, minor, patch = map(int, parts)
                return f"{major}.{minor}.{patch + 1}"
        except ValueError:
            pass
        return f"{version}.1"
    
    def compare_versions(self, v1: str, v2: str) -> int:
        """比较两个版本：返回 -1/0/1"""
        if self.scheme == "semver":
            sv1 = SemanticVersion.parse(v1)
            sv2 = SemanticVersion.parse(v2)
            if sv1 < sv2:
                return -1
            elif sv1 > sv2:
                return 1
            return 0
        else:
            # 简单比较
            k1 = self._version_key(v1)
            k2 = self._version_key(v2)
            if k1 < k2:
                return -1
            elif k1 > k2:
                return 1
            return 0
    
    def is_newer(self, version: str, base_version: str) -> bool:
        """检查版本是否比基础版本新"""
        return self.compare_versions(version, base_version) > 0
    
    def get_latest(self, versions: List[str]) -> Optional[str]:
        """获取最新版本"""
        if not versions:
            return None
        return max(versions, key=self._version_key)
    
    def sort_versions(self, versions: List[str], reverse: bool = True) -> List[str]:
        """版本排序"""
        return sorted(versions, key=self._version_key, reverse=reverse)


class ModelVersionManager:
    """模型专用版本管理"""
    
    def __init__(self, version_manager: VersionManager = None):
        self.vm = version_manager or VersionManager("semver")
    
    def suggest_version(self, model_name: str, existing_versions: List[str], change_type: str = "patch") -> str:
        """建议下一个版本号
        
        change_type: major/minor/patch
        - major: 破坏性变更（模型架构变化、输入输出格式变化）
        - minor: 新增功能（新增指标、支持新数据格式）
        - patch: 修复/优化（超参数调整、性能提升）
        """
        return self.vm.generate_next_version(existing_versions, change_type)
    
    def get_version_history(self, model_name: str, versions: List[Dict]) -> List[Dict]:
        """获取版本历史（按时间倒序）"""
        sorted_versions = self.vm.sort_versions([v["version"] for v in versions])
        version_map = {v["version"]: v for v in versions}
        return [version_map[v] for v in sorted_versions if v in version_map]
    
    def find_compatible_version(
        self, 
        model_name: str, 
        required_version: str, 
        available_versions: List[str]
    ) -> Optional[str]:
        """查找兼容版本（满足最低版本要求的最高版本）"""
        required = SemanticVersion.parse(required_version) if self.vm.scheme == "semver" else None
        
        compatible = []
        for v in available_versions:
            try:
                if self.vm.scheme == "semver":
                    parsed = SemanticVersion.parse(v)
                    if parsed >= required:
                        compatible.append(v)
                else:
                    if self._version_key(v) >= self._version_key(required_version):
                        compatible.append(v)
            except ValueError:
                continue
        
        return self.vm.get_latest(compatible) if compatible else None