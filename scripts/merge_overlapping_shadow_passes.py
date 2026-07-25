from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch_interpreter(root: Path) -> None:
    path = root / "libultraship/src/fast/interpreter.cpp"
    text = path.read_text(encoding="utf-8")

    function_start = text.find("void Interpreter::RenderShadowVolumes() {")
    function_end = text.find("void Interpreter::GfxDpSetGrayscaleColor", function_start)
    if function_start < 0 or function_end < 0:
        raise RuntimeError("shadow composite signaling: RenderShadowVolumes bounds not found")

    function = text[function_start:function_end]
    marker = "    // Large dynamic casters join the world cache only for this frame's weak-fill resolve."
    if marker not in function:
        marker = "    // Resolve static/world and physically large casters first."
    if marker not in function:
        raise RuntimeError("shadow composite signaling: first-layer marker not found")

    if "bool beginShadowComposite = true;" not in function:
        function = function.replace(
            marker,
            "    // The first active layer clears the reserved stencil bit. Every later layer tests that bit so\n"
            "    // overlapping world/actor/Navi shadows merge at one darkness instead of multiplying black.\n"
            "    bool beginShadowComposite = true;\n\n" + marker,
            1,
        )

    old_tail = "kDynamicShadowMapBias, kDynamicShadowMapPcfRadius);"
    call_count = function.count(old_tail)
    if call_count < 2 or call_count > 8:
        raise RuntimeError(f"shadow composite signaling: unexpected render-call count {call_count}")

    new_tail = (
        "kDynamicShadowMapBias,\n"
        "            beginShadowComposite ? -(kDynamicShadowMapPcfRadius + 1) : kDynamicShadowMapPcfRadius);\n"
        "        beginShadowComposite = false;"
    )
    function = function.replace(old_tail, new_tail)

    text = text[:function_start] + function + text[function_end:]
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_dx11_backend(root: Path) -> None:
    path = root / "libultraship/src/fast/backends/gfx_direct3d11_shadow_map.inc"
    text = path.read_text(encoding="utf-8")

    signature = '''void GfxRenderingAPIDX11::RenderDynamicShadowMap(const float* worldVertices, size_t vertexCount,
                                                 const float* cameraWorldToClip, const float lightDirection[3],
                                                 const float shadowAnchor[3], const float localLight[4],
                                                 uint32_t resolution, float opacity, float bias, int pcfRadius) {'''
    if "const bool beginShadowComposite = pcfRadius < 0;" not in text:
        text = replace_once(
            text,
            signature,
            signature
            + '''
    // Interpreter encodes the first active shadow layer with a negative PCF value. Decode it here and clear
    // the reserved stencil bit exactly once, so subsequent layers can skip pixels that are already shadowed.
    const bool beginShadowComposite = pcfRadius < 0;
    if (beginShadowComposite) {
        pcfRadius = -pcfRadius - 1;
    }''',
            "DX11 shadow composite begin signal",
        )

    old_resolve_state = '''        depthDesc.DepthEnable = FALSE;
        depthDesc.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ZERO;
        depthDesc.DepthFunc = D3D11_COMPARISON_ALWAYS;
        ThrowIfFailed(mDevice->CreateDepthStencilState(&depthDesc, mDynamicShadowResolveDepthState.GetAddressOf()));'''
    new_resolve_state = '''        depthDesc.DepthEnable = FALSE;
        depthDesc.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ZERO;
        depthDesc.DepthFunc = D3D11_COMPARISON_ALWAYS;
        // Reserve stencil bit 7 for dynamic-shadow composition. A fragment passes only while the bit is clear,
        // then REPLACE writes it. This is equivalent to combining shadow visibility with min() instead of
        // repeatedly alpha-blending black over black.
        depthDesc.StencilEnable = TRUE;
        depthDesc.StencilReadMask = 0x80;
        depthDesc.StencilWriteMask = 0x80;
        depthDesc.FrontFace.StencilFailOp = D3D11_STENCIL_OP_KEEP;
        depthDesc.FrontFace.StencilDepthFailOp = D3D11_STENCIL_OP_KEEP;
        depthDesc.FrontFace.StencilPassOp = D3D11_STENCIL_OP_REPLACE;
        depthDesc.FrontFace.StencilFunc = D3D11_COMPARISON_NOT_EQUAL;
        depthDesc.BackFace = depthDesc.FrontFace;
        ThrowIfFailed(mDevice->CreateDepthStencilState(&depthDesc, mDynamicShadowResolveDepthState.GetAddressOf()));'''
    text = replace_once(text, old_resolve_state, new_resolve_state, "DX11 overlap stencil state")

    old_saved_targets = '''    ComPtr<ID3D11RenderTargetView> savedRtv;
    ComPtr<ID3D11DepthStencilView> savedDsv;
    mContext->OMGetRenderTargets(1, savedRtv.GetAddressOf(), savedDsv.GetAddressOf());'''
    new_saved_targets = '''    ComPtr<ID3D11RenderTargetView> savedRtv;
    ComPtr<ID3D11DepthStencilView> savedDsv;
    mContext->OMGetRenderTargets(1, savedRtv.GetAddressOf(), savedDsv.GetAddressOf());
    if (beginShadowComposite && savedDsv) {
        // World-light volumes have already been drawn at this point. Clear stencil for the shadow layers so the
        // first real shadow owns each pixel and later overlapping layers merge without additional darkening.
        mContext->ClearDepthStencilView(savedDsv.Get(), D3D11_CLEAR_STENCIL, 1.0f, 0);
    }'''
    text = replace_once(text, old_saved_targets, new_saved_targets, "DX11 stencil clear before first shadow layer")

    text = replace_once(
        text,
        "    mContext->OMSetRenderTargets(1, &resolveRtv, nullptr);",
        "    mContext->OMSetRenderTargets(1, &resolveRtv, savedDsv.Get());",
        "DX11 bind scene stencil during shadow resolve",
    )
    text = replace_once(
        text,
        "    mContext->OMSetDepthStencilState(mDynamicShadowResolveDepthState.Get(), 0);",
        "    mContext->OMSetDepthStencilState(mDynamicShadowResolveDepthState.Get(), 0x80);",
        "DX11 reserved shadow stencil reference",
    )

    old_shader = '''    const bool casterReceiverOnly = localLight.w <= -4096.0;
    if (casterReceiverOnly && world.y > localLight.x) discard;

    float localFill = 0.0;
    const float localRadius = casterReceiverOnly ? 0.0 : abs(localLight.w);
    if (localRadius > 0.0) {
        localFill = saturate(1.0 - length(world.xyz - localLight.xyz) / localRadius);
        localFill = localFill * localFill * (3.0 - 2.0 * localFill);
    }
    const float localFillStrength = (!casterReceiverOnly && localLight.w < 0.0) ? 0.35 : 0.85;
    const float localShadowScale = 1.0 - localFill * localFillStrength;
    return float4(0.0, 0.0, 0.0,
                  saturate(shadowParams.x * shadow * edgeFade * localShadowScale));'''
    new_shader = '''    const bool casterReceiverOnly = localLight.w <= -4096.0;
    float receiverFade = 1.0;
    if (casterReceiverOnly) {
        // Keep the caster body excluded, but fade the receiver cutoff over a short vertical band so a platform
        // edge cannot reveal a hard rectangular boundary in the projected actor shadow.
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
    // Zero-alpha fragments must be discarded; otherwise they would still write stencil and incorrectly block a
    // later layer that contains a real shadow at the same pixel.
    if (resolvedShadowAlpha <= 0.01) discard;
    return float4(0.0, 0.0, 0.0, resolvedShadowAlpha);'''
    shader_count = text.count(old_shader)
    if shader_count != 2:
        raise RuntimeError(f"DX11 merged shadow shader: expected two matches, found {shader_count}")
    text = text.replace(old_shader, new_shader)

    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    patch_interpreter(root)
    patch_dx11_backend(root)
    print(
        "Merged overlapping dynamic-shadow layers through stencil and softened the Navi caster receiver cutoff."
    )


if __name__ == "__main__":
    main()
