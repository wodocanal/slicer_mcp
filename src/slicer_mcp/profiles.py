"""Read-only Orca preset discovery and inheritance resolution."""

import json
from dataclasses import dataclass
from pathlib import Path

from slicer_mcp.models import SlicerError

KINDS = ("machine", "process", "filament")


@dataclass(frozen=True)
class Profile:
    id: str
    path: Path
    kind: str
    scope: str
    data: dict


class ProfileCatalog:
    def __init__(self, roots: dict[str, Path]):
        self.roots = roots
        self._profiles: dict[str, Profile] | None = None

    @property
    def profiles(self) -> dict[str, Profile]:
        if self._profiles is None:
            found = {}
            for source, root in self.roots.items():
                for path in sorted(root.rglob("*.json")):
                    if path.parent.name not in KINDS:
                        continue
                    try:
                        data = json.loads(path.read_text())
                    except (ValueError, OSError):
                        continue
                    if not isinstance(data, dict) or not data.get("name"):
                        continue
                    relative = path.relative_to(root)
                    id_ = f"{source}:{relative.as_posix()}"
                    found[id_] = Profile(
                        id_, path, path.parent.name, f"{source}:{relative.parent}", data
                    )
            self._profiles = found
        return self._profiles

    def get(self, profile_id: str) -> Profile:
        try:
            return self.profiles[profile_id]
        except KeyError:
            raise SlicerError(f"Unknown profile ID: {profile_id}; use list_profiles") from None

    def list(self, kind: str, query: str = "", limit: int = 30) -> dict:
        if kind not in KINDS:
            raise SlicerError(f"Profile kind must be one of {KINDS}")
        matches = [
            p
            for p in self.profiles.values()
            if p.kind == kind
            and p.data.get("instantiation", "true") != "false"
            and query.casefold() in p.data["name"].casefold()
        ]
        return {
            "total": len(matches),
            "profiles": [
                {
                    "id": p.id,
                    "name": p.data["name"],
                    "kind": p.kind,
                    "inherits": p.data.get("inherits", ""),
                }
                for p in matches[:limit]
            ],
            "truncated": len(matches) > limit,
        }

    def resolve(self, profile_id: str, chain: tuple[str, ...] = ()) -> dict:
        if profile_id in chain or len(chain) >= 30:
            raise SlicerError(f"Cyclic or excessive profile inheritance: {profile_id}")
        profile = self.get(profile_id)
        parent_name = profile.data.get("inherits")
        result = {}
        if parent_name:
            candidates = [
                p
                for p in self.profiles.values()
                if p.kind == profile.kind and p.data["name"] == parent_name
            ]
            local = [p for p in candidates if p.scope == profile.scope]
            # Prefer the same vendor/account. Never guess among ambiguous parents.
            candidates = local or candidates
            if len(candidates) != 1:
                raise SlicerError(
                    f"Parent {parent_name!r} for {profile_id} is missing or ambiguous"
                )
            result.update(self.resolve(candidates[0].id, (*chain, profile_id)))
        result.update(profile.data)
        result.pop("inherits", None)
        return result
