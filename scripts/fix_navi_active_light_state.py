from __future__ import annotations

import re
from pathlib import Path


def replace_regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one structural match, found {count}")
    return updated


def patch_navi_source_light(root: Path) -> None:
    path = root / "soh/src/overlays/actors/ovl_En_Elf/z_en_elf.c"
    text = path.read_text(encoding="utf-8")

    marker = "PLAYER_STATE2_NAVI_ACTIVE is the authoritative gameplay state"
    if marker not in text:
        replacement = r'''    glowLightRadius = 100;

    // player->naviActor remains allocated while Navi is hidden. PLAYER_STATE2_NAVI_ACTIVE is the authoritative gameplay state changed by the vanish/return logic, so the emitted point light must follow it too.
    player = GET_PLAYER(play);
    if ((this->actor.params == FAIRY_NAVI &&
         (player == NULL || (player->stateFlags2 & PLAYER_STATE2_NAVI_ACTIVE) == 0 || this->unk_2A8 == 7 ||
          this->unk_2A8 == 8 || this->actor.scale.x <= 0.00008f)) ||
        (this->actor.params != FAIRY_NAVI && this->unk_2A8 == 8)) {
        glowLightRadius = 0;
    }'''
        text = replace_regex_once(
            text,
            r"    glowLightRadius = 100;\s*\n\s*if \(this->unk_2A8 == 8\) \{\s*\n\s*glowLightRadius = 0;\s*\n\s*\}",
            replacement,
            "Navi source-light active-state gate",
        )

    path.write_text(text, encoding="utf-8", newline="\n")


def patch_shadow_fill_gate(root: Path) -> None:
    path = root / "soh/soh/Enhancements/Graphics/ZZNaviShadowLightFix.cpp"
    text = path.read_text(encoding="utf-8")

    if "const bool naviGameplayActive" not in text:
        replacement = r'''                const bool naviGameplayActive =
                    (player->stateFlags2 & PLAYER_STATE2_NAVI_ACTIVE) != 0;
                const bool visiblyEmitting = naviGameplayActive && navi->unk_2A8 != 7 && navi->unk_2A8 != 8 &&
                                               (navi->fairyFlags & 8) == 0 && visualScale > 0.01f &&
                                               emittedRadius > 0.01f;'''
        text = replace_regex_once(
            text,
            r"\s*const bool visiblyEmitting = navi->unk_2A8 != 8 && \(navi->fairyFlags & 8\) == 0 &&\s*visualScale > 0\.01f && emittedRadius > 0\.01f;",
            "\n" + replacement,
            "Navi renderer active-state gate",
        )

    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    patch_navi_source_light(root)
    patch_shadow_fill_gate(root)
    print("Bound Navi world light and shadow fill to PLAYER_STATE2_NAVI_ACTIVE.")


if __name__ == "__main__":
    main()
