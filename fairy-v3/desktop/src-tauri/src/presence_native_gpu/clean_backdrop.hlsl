Texture2D<float4> captured_texture : register(t0);
Texture2D<float4> previous_clean_texture : register(t1);
Texture2D<float4> previous_overlay_texture : register(t2);
SamplerState backdrop_sampler : register(s0);

cbuffer CleanBackdropConstants : register(b0) {
    float2 capture_size;
    float2 overlay_origin_px;
    float2 overlay_size_px;
    float2 input_origin_px;
    float2 input_size_px;
    float previous_clean_valid;
    float previous_overlay_valid;
    float capture_linear;
    float input_surface_valid;
    float drag_active;
    float constants_padding;
};

struct VertexOutput {
    float4 position : SV_Position;
    float2 uv : TEXCOORD0;
};

VertexOutput clean_vs_main(uint vertex_id : SV_VertexID) {
    VertexOutput output;
    float2 position = float2((vertex_id << 1) & 2, vertex_id & 2);
    output.uv = position;
    output.position = float4(position * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0);
    return output;
}

float inside_rectangle(float2 point_px, float2 origin_px, float2 size_px) {
    float2 lower = step(origin_px, point_px);
    float2 upper = step(point_px, origin_px + max(size_px - 1.0.xx, 0.0.xx));
    return lower.x * lower.y * upper.x * upper.y;
}

float3 bounded_low_alpha_recovery(float3 captured, float4 overlay, float3 previous_clean) {
    // Only invert low-alpha pixels. The denominator is always at least 0.64, so timestamp or
    // position jitter cannot amplify a rim pixel into the white bars produced by the old pass.
    float denominator = max(1.0 - overlay.a, 0.64);
    float3 recovered = (captured - overlay.rgb) / denominator;
    float3 below_gamut = max(-recovered, 0.0.xxx);
    float3 above_gamut = max(recovered - 1.0.xxx, 0.0.xxx);
    float gamut_error = max(
        max(below_gamut.r, max(below_gamut.g, below_gamut.b)),
        max(above_gamut.r, max(above_gamut.g, above_gamut.b))
    );
    float confidence = 1.0 - smoothstep(0.01, 0.08, gamut_error);
    float recovery_weight = (1.0 - smoothstep(0.14, 0.36, overlay.a)) * confidence;
    float3 limited = clamp(saturate(recovered), previous_clean - 0.42, previous_clean + 0.42);
    return lerp(captured, limited, recovery_weight);
}

float4 clean_ps_main(VertexOutput input) : SV_Target {
    float2 pixel = input.uv * capture_size;
    float4 captured = captured_texture.Sample(backdrop_sampler, input.uv);
    if (previous_clean_valid < 0.5) {
        return float4(captured.rgb, 1.0);
    }

    float3 previous_clean = previous_clean_texture.Sample(backdrop_sampler, input.uv).rgb;
    if (
        input_surface_valid > 0.5 &&
        inside_rectangle(pixel, input_origin_px, input_size_px) > 0.5
    ) {
        // DOM text and controls are not part of the optical material. Keep them out of the clean
        // cache so they can never become recursive content inside the native glass surface.
        return float4(previous_clean, 1.0);
    }
    if (
        previous_overlay_valid < 0.5 ||
        inside_rectangle(pixel, overlay_origin_px, overlay_size_px) < 0.5
    ) {
        return float4(captured.rgb, 1.0);
    }

    float2 overlay_uv = saturate((pixel - overlay_origin_px) / max(overlay_size_px, 1.0.xx));
    float4 overlay = previous_overlay_texture.Sample(backdrop_sampler, overlay_uv);
    if (overlay.a <= 0.002) {
        return float4(captured.rgb, 1.0);
    }

    if (capture_linear > 0.5) {
        float preserve = smoothstep(0.02, 0.20, overlay.a);
        return float4(lerp(captured.rgb, previous_clean, preserve), 1.0);
    }

    float3 live_background = bounded_low_alpha_recovery(
        saturate(captured.rgb),
        overlay,
        saturate(previous_clean)
    );
    // High-alpha rim pixels are not invertible. Retain the prior clean desktop there, while the
    // clear center continues to update from the current monitor frame. Dragging widens the
    // conservative band to absorb one-frame WGC/compositor timing differences.
    float preserve_start = drag_active > 0.5 ? 0.18 : 0.24;
    float preserve_end = drag_active > 0.5 ? 0.42 : 0.56;
    float preserve = smoothstep(preserve_start, preserve_end, overlay.a);
    return float4(lerp(live_background, previous_clean, preserve), 1.0);
}
