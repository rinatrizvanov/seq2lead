"""The docking contract, loaded from YAML and carried by digest.

Every number that decides the gate -- the box, the exhaustiveness, the effect
threshold, the decision rule -- lives in the config file and is frozen before
the cohort is docked. Nothing here reads a result.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_PATH = Path("configs/experiments/m10-docking-v1.yaml")


class ContractError(RuntimeError):
    """The config does not state something the gate is not allowed to assume."""


@dataclass(frozen=True)
class DockingConfig:
    version: str
    config_sha256: str
    path: Path
    raw: dict[str, Any] = field(repr=False)

    # -- the parts the rest of the code reads by name, so a typo in the YAML
    #    fails here rather than silently defaulting somewhere downstream.
    @property
    def target_id(self) -> int:
        return int(self._req("target", "id"))

    @property
    def endpoint_id(self) -> int:
        return int(self._req("endpoint", "id"))

    @property
    def endpoint_name(self) -> str:
        return str(self._req("endpoint", "name"))

    @property
    def box_center(self) -> tuple[float, float, float]:
        c = self._req("protocol", "box", "center")
        return (float(c[0]), float(c[1]), float(c[2]))

    @property
    def box_size(self) -> tuple[float, float, float]:
        s = self._req("protocol", "box", "size")
        return (float(s[0]), float(s[1]), float(s[2]))

    @property
    def exhaustiveness(self) -> int:
        return int(self._req("protocol", "exhaustiveness"))

    @property
    def num_modes(self) -> int:
        return int(self._req("protocol", "num_modes"))

    @property
    def seed(self) -> int:
        return int(self._req("protocol", "seed"))

    @property
    def engine_sha256(self) -> str:
        return str(self._req("engine", "sha256"))

    @property
    def engine_version(self) -> str:
        return str(self._req("engine", "version"))

    @property
    def effect_threshold(self) -> float:
        return float(self._req("gate", "effect_threshold"))

    @property
    def minimum_usable_per_class(self) -> int:
        return int(self._req("gate", "minimum_usable_per_class"))

    @property
    def bootstrap_resamples(self) -> int:
        return int(self._req("gate", "uncertainty", "resamples"))

    @property
    def bootstrap_level(self) -> float:
        return float(self._req("gate", "uncertainty", "level"))

    @property
    def bootstrap_seed(self) -> int:
        return int(self._req("gate", "uncertainty", "seed"))

    @property
    def cohort_spec(self) -> dict[str, Any]:
        return dict(self._req("cohort"))

    @property
    def expected_cohort_content_sha256(self) -> str:
        """The frozen digest of what the cohort must contain.

        Read from the contract, never recomputed from the cohort being checked --
        deriving expected and actual from the same file would verify nothing.
        """
        return str(self._req("cohort_pin", "content_sha256"))

    @property
    def expected_cohort_counts(self) -> tuple[int, int]:
        pin = self._req("cohort_pin")
        return (int(pin["n_active"]), int(pin["n_inactive"]))

    def selection_sha256(self) -> str:
        """Digest of the fields that decide WHICH compounds are in the cohort.

        Deliberately narrower than `config_sha256`. Exhaustiveness, box size and
        the decision threshold do not change who is in the cohort, so tightening
        one of those must not invalidate a frozen cohort -- while a change to the
        endpoint, the eligibility rule or the sampling must.
        """
        import hashlib

        body = json.dumps(
            {
                "target_id": self._req("target", "id"),
                "endpoint": self._req("endpoint"),
                "eligibility": self._req("eligibility"),
                "cohort": self._req("cohort"),
            },
            sort_keys=True,
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def _req(self, *keys: str) -> Any:
        node: Any = self.raw
        seen: list[str] = []
        for k in keys:
            seen.append(k)
            if not isinstance(node, dict) or k not in node:
                raise ContractError(
                    f"{self.path} does not declare {'.'.join(seen)}. The gate refuses to "
                    "supply a default for a value that decides its own outcome."
                )
            node = node[k]
        return node


def load_docking_config(path: Path = CONFIG_PATH) -> DockingConfig:
    import yaml

    if not path.exists():
        raise ContractError(f"no docking contract at {path}")
    body = path.read_bytes()
    raw = yaml.safe_load(body.decode("utf-8"))
    if not isinstance(raw, dict) or "version" not in raw:
        raise ContractError(f"{path} is not a docking contract (no `version`)")
    return DockingConfig(
        version=str(raw["version"]),
        config_sha256=hashlib.sha256(body).hexdigest(),
        path=path,
        raw=raw,
    )
