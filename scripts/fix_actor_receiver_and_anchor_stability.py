from __future__ import annotations

from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "libultraship/src/fast/backends/gfx_direct3d11_shadow_map.inc"
    text = path.read_text(encoding="utf-8")

    old_shader = """    const bool casterReceiverOnly = localLight.w <= -4096.0;
    float receiverFade = 1.0;
    if (casterReceiverOnly) {
        // Fade the receiver cutoff over a short vertical band so a platform edge cannot reveal a hard rectangle.
        const float receiverFadeHeight = 12.0;
        receiverFade = saturate((localLight.x + receiverFadeHeight - world.y) / receiverFadeHeight);
        if (receiverFade <= 0.001) discard;
    }

    float localFill = 0.0;
    const float localRadius = casterReceiverOnly ? 0.0 : abs(localLight.w);
    if (localRadius > 0.0) {
        localFill = saturate(1.0 - length(world.xyz - localLight.xyz) / localRadius);
        localFill = localFill * localFill * (3.0 - 2.0 * localFill);
    }
    const float localFillStrength = (!casterReceiverOnly && localLight.w < 0.0) ? 0.35 : 0.85;
    const float localShadowScale = 1.0 - localFill * localFillStrength;
    const float resolvedShadowAlpha =
        saturate(shadowParams.x * shadow * edgeFade * localShadowScale * receiverFade);
    // Zero-alpha fragments must not write stencil, or they would block a later real shadow at the same pixel.
    if (resolvedShadowAlpha <= 0.01) discard;
    return float4(0.0, 0.0, 0.0, resolvedShadowAlpha);"""

    new_shader = """    const bool casterReceiverOnly = localLight.w <= -4096.0;
    float receiverFade = 1.0;
    if (casterReceiverOnly) {
        // X stores collision-floor Y, Y/Z store the texel-snapped receiver centre and W stores
        // -(8192 + radius). Only the horizontal ground band beneath this caster can receive its own shadow.
        const float receiverFloorY = localLight.x;
        const float2 receiverCenter = float2(localLight.y, localLight.z);
        const float receiverRadius = max(24.0, -localLight.w - 8192.0);
        const float aboveFloor = world.y - receiverFloorY;
        const float belowFloor = receiverFloorY - world.y;
        const float maximumAboveFloor = 4.0;
        const float maximumBelowFloor = 28.0;
        if (aboveFloor > maximumAboveFloor || belowFloor > maximumBelowFloor) discard;

        const float verticalFade =
            aboveFloor > 0.0 ? saturate(1.0 - aboveFloor / maximumAboveFloor)
                             : saturate(1.0 - belowFloor / maximumBelowFloor);
        const float receiverDistance = length(world.xz - receiverCenter);
        if (receiverDistance >= receiverRadius) discard;
        const float radialFade = saturate((receiverRadius - receiverDistance) / 12.0);
        receiverFade = verticalFade * radialFade;
        if (receiverFade <= 0.01) discard;
    }

    float localFill = 0.0;
    const float localRadius = casterReceiverOnly ? 0.0 : abs(localLight.w);
    if (localRadius > 0.0) {
        localFill = saturate(1.0 - length(world.xyz - localLight.xyz) / localRadius);
        localFill = localFill * localFill * (3.0 - 2.0 * localFill);
    }
    const float localFillStrength = (!casterReceiverOnly && localLight.w < 0.0) ? 0.35 : 0.85;
    const float localShadowScale = 1.0 - localFill * localFillStrength;
    const float resolvedShadowAlpha =
        saturate(shadowParams.x * shadow * edgeFade * localShadowScale * receiverFade);
    if (resolvedShadowAlpha <= 0.01) discard;
    return float4(0.0, 0.0, 0.0, resolvedShadowAlpha);"""

    matches = text.count(old_shader)
    if matches != 2:
        raise RuntimeError(f"bounded compact receiver shader: expected two merged shader blocks, found {matches}")

    path.write_text(text.replace(old_shader, new_shader), encoding="utf-8", newline="\n")
    print("Restricted compact self-shadow receivers to a floor-locked vertical band and horizontal radius.")


if __name__ == "__main__":
    main()
