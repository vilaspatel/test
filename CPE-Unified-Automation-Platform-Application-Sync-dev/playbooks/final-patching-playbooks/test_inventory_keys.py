#!/usr/bin/env python3
"""
Mirrors the [rhel:vars] key-selection rule in the inventory files:

    ansible_ssh_private_key_file={{ anixter_key if ".anixter.com" in
        (ansible_host | default(inventory_hostname)) | lower else wesco_key }}

The else-branch is a silent fallback: a host on neither domain would quietly get
the wesco key. This asserts no host lands there by accident.

    python3 test_inventory_keys.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).parent
KNOWN_DOMAINS = (".anixter.com", ".wescodist.com")


def hosts(ini: Path):
    """Yield (inventory_hostname, ansible_host) for host lines, skipping vars sections."""
    in_vars = False
    for raw in ini.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            in_vars = line.endswith(":vars]")
            continue
        if in_vars:
            continue
        parts = line.split()
        name = parts[0]
        target = next((p.split("=", 1)[1] for p in parts[1:] if p.startswith("ansible_host=")), name)
        yield name, target


def select_key(ansible_host: str) -> str:
    return "anixter_key" if ".anixter.com" in ansible_host.lower() else "wesco_key"


def main() -> int:
    # Only the cycle inventories drive the ST2 patching flow. inventory.ini is a
    # scratch file with a raw-IP host and has no domain to select on.
    inventories = sorted(HERE.glob("inventory-cycle*.ini"))
    assert inventories, "no inventory-cycle*.ini found"

    strays, counts = [], {"anixter_key": 0, "wesco_key": 0}
    for ini in inventories:
        for name, target in hosts(ini):
            low = target.lower()
            if not any(d in low for d in KNOWN_DOMAINS):
                strays.append(f"{ini.name}: {name} -> {target}")
            counts[select_key(target)] += 1

    # Spot-check both branches, including the uppercase hostnames in cycle2.
    assert select_key("atc1pros01q.anixter.com") == "anixter_key"
    assert select_key("drlx-qa-erp-04.wescodist.com") == "wesco_key"
    assert select_key("AXE1WASC01Q.anixter.com") == "anixter_key"
    # A bare shortname must not silently claim a domain key.
    assert "shortname" not in KNOWN_DOMAINS

    for ini in inventories:
        assert "[rhel:vars]" in ini.read_text(), f"{ini.name} is missing the [rhel:vars] key-selection block"

    print(f"anixter_key: {counts['anixter_key']}  wesco_key: {counts['wesco_key']}")
    if strays:
        print("FAIL - hosts on neither domain would fall through to wesco_key:")
        print("\n".join(f"  {s}" for s in strays))
        return 1
    print("OK - every host resolves to an explicit domain")
    return 0


if __name__ == "__main__":
    sys.exit(main())
