from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "libultraship/src/fast/interpreter.cpp"
    text = path.read_text(encoding="utf-8")

    old = "        const bool foliageCard = (mRsp->geometry_mode & G_LIGHTING) == 0;\n"
    new = '''        const bool unlitGeometry = (mRsp->geometry_mode & G_LIGHTING) == 0;
        bool shadowUseAlpha =
            ((mRdp->other_mode_l & (3 << 20)) == (G_BL_CLR_MEM << 20) &&
             (mRdp->other_mode_l & (3 << 16)) == (G_BL_1MA << 16)) ||
            ((mRdp->other_mode_l & (3 << 22)) == (G_BL_CLR_MEM << 22) &&
             (mRdp->other_mode_l & (3 << 18)) == (G_BL_1MA << 18));
        bool shadowTextureEdge = (mRdp->other_mode_l & CVG_X_ALPHA) == CVG_X_ALPHA;
        bool shadowAlphaThreshold =
            (mRdp->other_mode_l & (3U << G_MDSFT_ALPHACOMPARE)) == G_AC_THRESHOLD;
        if (shadowTextureEdge) {
            shadowUseAlpha = true;
        }
        // Only translucent, unlit triangles are canopy cards. This keeps ordinary unlit opaque pieces
        // from other actors on the direct geometry path instead of accidentally alpha-cutting them.
        const bool foliageCard = unlitGeometry && (shadowUseAlpha || shadowTextureEdge || shadowAlphaThreshold);
'''

    count = text.count(old)
    if count == 1:
        text = text.replace(old, new, 1)
    elif "Only translucent, unlit triangles are canopy cards" not in text:
        raise RuntimeError(f"foliage refinement: expected one capture mode line, found {count}")

    path.write_text(text, encoding="utf-8", newline="\n")
    print("Restricted alpha-tested shadow capture to translucent unlit foliage cards.")


if __name__ == "__main__":
    main()
