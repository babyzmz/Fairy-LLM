Texture2D<float4> desktop_texture : register(t0);
SamplerState desktop_sampler : register(s0);

static const float EDGE_LENS_DEPTH_PX = 52.0;
static const float EDGE_REFRACTION_PX = 42.0;
static const float EDGE_DISPERSION_PX = 8.5;

cbuffer PresenceConstants : register(b0) {
    float2 output_size;
    float2 capture_size;
    float2 source_origin_px;
    float2 configured_core_center_px;
    float2 configured_capsule_center_px;
    float elapsed_seconds;
    float surface_scale;
    float shape_droplet;
    float shape_bridge;
    float shape_capsule;
    float expansion_direction;
    float visual_state;
    float material_opacity;
    float voice_level;
    float reduced_motion;
    float reduced_transparency;
    float increased_contrast;
    float particles_enabled;
    float diagnostic_solid;
    float capture_linear;
    float capsule_half_width;
    float state_elapsed_seconds;
    float drag_active;
    float capture_source_valid;
    float state_energy;
    float state_pulse;
    float state_notify_wave;
    float state_voice_mix;
    float3 state_accent;
    float state_transition_progress;
};

struct VertexOutput {
    float4 position : SV_Position;
    float2 uv : TEXCOORD0;
};

VertexOutput vs_main(uint vertex_id : SV_VertexID) {
    VertexOutput output;
    float2 position = float2((vertex_id << 1) & 2, vertex_id & 2);
    output.uv = position;
    output.position = float4(position * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0);
    return output;
}

float3 srgb_to_linear(float3 color) {
    float3 low = color / 12.92;
    float3 high = pow((color + 0.055) / 1.055, 2.4);
    return lerp(low, high, step(0.04045, color));
}

float3 linear_to_srgb(float3 color) {
    float3 low = color * 12.92;
    float3 high = 1.055 * pow(max(color, 0.0), 1.0 / 2.4) - 0.055;
    return lerp(low, high, step(0.0031308, color));
}

float3 sample_desktop_linear(float2 uv) {
    // WGC supplies one monitor texture. Restrict every shader lookup to the pet surface plus a
    // small optical guard band so refraction cannot address unrelated regions of that texture.
    float optical_guard_px = 52.0 * max(surface_scale, 0.01);
    float2 minimum_uv = (source_origin_px - optical_guard_px) / capture_size;
    float2 maximum_uv = (source_origin_px + output_size + optical_guard_px) / capture_size;
    uv = clamp(uv, minimum_uv, maximum_uv);
    float3 color = desktop_texture.Sample(desktop_sampler, uv).rgb;
    if (capture_linear > 0.5) {
        color = max(color, 0.0);
        float peak = max(color.r, max(color.g, color.b));
        return color / max(1.0, peak);
    }
    return srgb_to_linear(saturate(color));
}

float circle_sdf(float2 position_px, float radius) {
    return length(position_px) - radius;
}

float rounded_box_sdf(float2 position_px, float2 half_extent, float radius) {
    float2 q = abs(position_px) - half_extent + radius;
    return min(max(q.x, q.y), 0.0) + length(max(q, 0.0)) - radius;
}

float segment_distance(float2 sample_position, float2 segment_start, float2 segment_end) {
    float2 segment = segment_end - segment_start;
    float progress = saturate(
        dot(sample_position - segment_start, segment) / max(dot(segment, segment), 0.0001)
    );
    return length(sample_position - segment_start - segment * progress);
}

float smooth_union(float a, float b, float radius) {
    float h = saturate(0.5 + 0.5 * (b - a) / radius);
    return lerp(b, a, h) - radius * h * (1.0 - h);
}

float direction_value() {
    return expansion_direction < 0.0 ? -1.0 : 1.0;
}

float2 core_center_px() {
    return configured_core_center_px;
}

float2 capsule_center_px() {
    return configured_capsule_center_px;
}

float core_sdf(float2 position_px) {
    return circle_sdf(position_px - core_center_px(), 72.0 * surface_scale);
}

float capsule_sdf(float2 position_px) {
    float morph = smoothstep(0.0, 1.0, saturate(shape_capsule));
    if (morph < 0.001) return 100000.0;
    float2 center_offset = capsule_center_px() - core_center_px();
    float2 center = lerp(
        core_center_px() + normalize(center_offset) * 82.0 * surface_scale,
        capsule_center_px(),
        morph
    );
    float half_width = lerp(2.0, capsule_half_width, morph) * surface_scale;
    float radius = lerp(2.0, 26.0, morph) * surface_scale;
    float half_segment = max(0.0, half_width - radius);
    return segment_distance(
        position_px,
        center - float2(half_segment, 0.0),
        center + float2(half_segment, 0.0)
    ) - radius;
}

float scene_sdf(float2 position_px) {
    float core = core_sdf(position_px);
    float2 center_offset = capsule_center_px() - core_center_px();
    float2 axis = normalize(center_offset);
    float2 normal_axis = float2(-axis.y, axis.x);
    float2 core_local = position_px - core_center_px();
    float2 local = float2(dot(core_local, axis), dot(core_local, normal_axis));
    float droplet_morph = smoothstep(0.0, 1.0, saturate(shape_droplet));
    float droplet_center = lerp(62.0, 104.0, droplet_morph) * surface_scale;
    float droplet_radius = lerp(2.0, 18.0, droplet_morph) * surface_scale;
    float droplet = circle_sdf(
        local - float2(droplet_center, 0.0),
        droplet_radius
    );
    float result = smooth_union(core, droplet, 11.0 * surface_scale);

    float bridge_morph = smoothstep(0.0, 1.0, saturate(shape_bridge));
    if (bridge_morph >= 0.001) {
        float target_distance = max(
            82.0 * surface_scale,
            length(center_offset) - capsule_half_width * surface_scale * 0.72
        );
        float bridge_end = lerp(64.0 * surface_scale, target_distance, bridge_morph);
        float bridge_radius = lerp(2.0, 20.0, bridge_morph) * surface_scale;
        float bridge = segment_distance(
            local,
            float2(52.0 * surface_scale, 0.0),
            float2(bridge_end, 0.0)
        ) - bridge_radius;
        result = smooth_union(result, bridge, 14.0 * surface_scale);
    }
    float capsule = capsule_sdf(position_px);
    return min(result, capsule);
}

float thickness_field(float signed_distance, float2 position_px) {
    float inside = saturate(-signed_distance / max(54.0 * surface_scale, 1.0));
    float edge = exp(-abs(signed_distance) / max(7.0 * surface_scale, 1.0));
    float bottom_mask = smoothstep(0.48, 0.88, position_px.y / max(output_size.y, 1.0));
    float bottom_lip = bottom_mask * exp(-abs(signed_distance) / max(12.0 * surface_scale, 1.0));
    return saturate(inside * 0.42 + edge * 0.72 + bottom_lip * 0.34);
}

float2 surface_normal(float2 position_px) {
    float epsilon = max(1.0, surface_scale);
    float2 gradient = float2(
        scene_sdf(position_px + float2(epsilon, 0.0)) - scene_sdf(position_px - float2(epsilon, 0.0)),
        scene_sdf(position_px + float2(0.0, epsilon)) - scene_sdf(position_px - float2(0.0, epsilon))
    );
    return normalize(gradient + float2(0.0001, 0.0001));
}

float edge_refraction_profile(float signed_distance) {
    float scale = max(surface_scale, 0.01);
    // The optical field is finite: the center samples the desktop at its original coordinate,
    // while the final 48 px build rapidly into the thick Apple-style refractive rim.
    float edge_distance = saturate(
        1.0 - abs(signed_distance) / max(EDGE_LENS_DEPTH_PX * scale, 1.0)
    );
    return pow(smoothstep(0.0, 1.0, edge_distance), 0.70);
}

float2 lens_warp_px(float2 position_px, float signed_distance, float2 normal) {
    float scale = max(surface_scale, 0.01);
    float edge_lens = edge_refraction_profile(signed_distance);
    float bottom_lip = smoothstep(0.54, 0.90, position_px.y / max(output_size.y, 1.0));
    float2 warp =
        normal * edge_lens * EDGE_REFRACTION_PX * scale
        + float2(0.0, bottom_lip * edge_lens * 7.0 * scale);
    if (reduced_transparency > 0.5) return warp * 0.35;
    return warp;
}

float state_energy_value() {
    return state_energy;
}

float3 state_accent_color() {
    return state_accent;
}

float state_pulse_value() {
    return state_pulse;
}

float notify_wave_value() {
    return state_notify_wave;
}

float hash21(float2 value) {
    value = frac(value * float2(123.34, 456.21));
    value += dot(value, value + 45.32);
    return frac(value.x * value.y);
}

float particle_sparkles(float2 position_px) {
    if (particles_enabled < 0.5) return 0.0;
    float scale = max(surface_scale, 0.01);
    float2 core_delta = (position_px - core_center_px()) / scale;
    float radius = length(core_delta);
    if (radius < 28.0 || radius > 59.0) return 0.0;
    float2 grid = core_delta / 12.0;
    float2 cell = floor(grid);
    float2 jitter = float2(hash21(cell), hash21(cell + 19.7));
    float2 delta = frac(grid) - jitter;
    float sparkle = exp(-dot(delta, delta) * 54.0);
    float motion_time = reduced_motion > 0.5 ? 0.0 : elapsed_seconds;
    float twinkle = 0.35 + 0.65 * sin(motion_time * 1.7 + hash21(cell + 7.1) * 6.28318);
    return sparkle * saturate(twinkle);
}

float4 ps_main(VertexOutput input) : SV_Target {
    if (diagnostic_solid > 0.5) {
        return float4(1.0, 0.0, 1.0, 1.0);
    }
    float2 local_px = input.uv * output_size;
    float signed_distance = scene_sdf(local_px);
    float scale = max(surface_scale, 0.01);
    if (signed_distance > 11.0 * scale) {
        return float4(0.0, 0.0, 0.0, 0.0);
    }

    float outside_shadow = exp(-max(signed_distance, 0.0) / max(3.8 * scale, 1.0))
        * saturate(signed_distance / max(1.5 * scale, 1.0))
        * 0.16 * material_opacity;
    if (signed_distance > 2.0 * scale) {
        return float4(0.0, 0.0, 0.0, outside_shadow);
    }

    float thickness = thickness_field(signed_distance, local_px);
    float2 normal = surface_normal(local_px);
    float2 base_uv = (source_origin_px + local_px) / capture_size;
    float edge_focus = edge_refraction_profile(signed_distance);
    float2 warp_uv = lens_warp_px(local_px, signed_distance, normal) / capture_size;
    float dispersion_px = EDGE_DISPERSION_PX * pow(edge_focus, 1.35);
    if (reduced_transparency > 0.5 || increased_contrast > 0.5) {
        dispersion_px = 0.0;
    }
    float2 dispersion_uv = normal * dispersion_px / capture_size;
    float2 tangent_uv = float2(-normal.y, normal.x) * (1.15 * edge_focus * scale) / capture_size;

    float3 refracted = float3(0.72, 0.82, 0.90);
    if (capture_source_valid > 0.5) {
        float red = sample_desktop_linear(base_uv + warp_uv + dispersion_uv).r;
        float green = sample_desktop_linear(base_uv + warp_uv).g;
        float blue = sample_desktop_linear(base_uv + warp_uv - dispersion_uv).b;
        refracted = float3(red, green, blue);
        float3 edge_blur = 0.5 * (
            sample_desktop_linear(base_uv + warp_uv + tangent_uv)
            + sample_desktop_linear(base_uv + warp_uv - tangent_uv)
        );
        refracted = lerp(refracted, edge_blur, 0.10 * edge_focus);
    }

    float3 view_vector = normalize(float3(
        (local_px - output_size * 0.5) / max(output_size.y, 1.0),
        1.35
    ));
    float3 surface_vector = normalize(float3(normal * thickness, 1.0 - thickness * 0.72));
    float fresnel = pow(1.0 - saturate(dot(view_vector, surface_vector)), 4.2);
    float edge_band = exp(-abs(signed_distance) / max(3.0 * scale, 1.0));
    float highlight_primary = pow(saturate(dot(normalize(float2(-0.74, -0.67)), -normal)), 14.0) * edge_band;
    float highlight_secondary = pow(saturate(dot(normalize(float2(0.66, 0.75)), -normal)), 24.0) * edge_band;
    float outer_caustic = exp(-abs(signed_distance + 7.0 * scale) / max(3.2 * scale, 1.0));
    float inner_caustic = exp(-abs(signed_distance + 20.0 * scale) / max(6.0 * scale, 1.0));
    float bottom_shadow = pow(saturate(normal.y), 6.0) * edge_band;

    float state_energy = state_energy_value();
    float luminance = dot(refracted, float3(0.2126, 0.7152, 0.0722));
    float adaptive_tint = lerp(0.035, 0.010, saturate(luminance));
    float material_tint = adaptive_tint + 0.045 * edge_focus;
    if (increased_contrast > 0.5) material_tint = 0.0;
    float3 color = lerp(refracted, float3(0.78, 0.92, 1.0), material_tint);
    if (reduced_transparency > 0.5) {
        float accessibility_fill = luminance > 0.58 ? 0.08 : 0.94;
        color = lerp(color, accessibility_fill.xxx, 0.20);
    }
    color += float3(1.0, 0.99, 0.97) * highlight_primary * 0.72;
    color += float3(0.68, 0.88, 1.0) * highlight_secondary * 0.46;
    color += float3(0.72, 0.95, 1.0) * outer_caustic * 0.34;
    color += float3(0.56, 0.84, 1.0) * inner_caustic * 0.16;
    color += float3(0.58, 0.86, 1.0) * fresnel * edge_band * 0.34;
    color += float3(0.82, 0.94, 1.0) * fresnel * thickness * 0.045;
    float spectral_polarity = dot(normal, normalize(float2(-0.76, 0.65)));
    float warm_dispersion = saturate(spectral_polarity) * edge_focus;
    float cool_dispersion = saturate(-spectral_polarity) * edge_focus;
    color += float3(0.17, 0.02, -0.09) * warm_dispersion;
    color += float3(-0.09, 0.02, 0.17) * cool_dispersion;
    if (increased_contrast > 0.5) {
        float contrast_tone = luminance > 0.58 ? 0.01 : 0.99;
        color = lerp(color, contrast_tone.xxx, saturate(edge_band * 0.24));
    }
    color *= 1.0 - bottom_shadow * 0.29;

    float core_radius = length(local_px - core_center_px()) / scale;
    float pulse = state_pulse_value();
    float voice_pulse = state_voice_mix;
    float notify_wave = notify_wave_value();
    float core_ring_outer = exp(-abs(core_radius - 40.0) / 2.2);
    float core_ring_inner = exp(-abs(core_radius - 24.0) / 2.0);
    float core_orb = exp(-(core_radius * core_radius) / 52.0);
    float3 accent = state_accent_color();
    float3 core_light = accent * (core_ring_outer * 0.62 + core_ring_inner * 0.32);
    core_light += float3(0.90, 0.98, 1.0)
        * core_orb
        * (0.14 + state_energy * 0.45 + voice_pulse * 0.20)
        * pulse;
    core_light += float3(1.0, 0.88, 0.62) * core_ring_outer * notify_wave * 0.22;
    core_light += float3(0.82, 0.96, 1.0) * particle_sparkles(local_px) * 0.24;
    // Preserve the sampled desktop through the center. Emission adapts to the captured
    // luminance and may only use available display headroom, so a bright backdrop cannot turn
    // the whole Fairy core into an opaque white disc.
    float core_background_adaptation = lerp(
        1.0,
        0.14,
        smoothstep(0.42, 0.88, luminance)
    );
    float3 core_headroom = max(0.0.xxx, 0.88.xxx - color);
    color += min(core_light * core_background_adaptation, core_headroom);
    // Keep Fairy's identity point legible independently of the clear glass center. The compact
    // Gaussian limits this emission to roughly 10-12 px and preserves the undistorted backdrop
    // everywhere else.
    float beacon_emission = core_orb
        * (0.48 + state_energy * 0.42 + voice_pulse * 0.12)
        * lerp(0.74, 1.0, pulse);
    color += max(0.0.xxx, float3(0.96, 0.985, 1.0) - color) * beacon_emission;

    float shape_mask = saturate((1.5 * scale - signed_distance) / (2.5 * scale));
    // The center behaves like clear glass and lets the real desktop remain the source of truth.
    // Opacity rises only through the broad optical rim, where the displaced sample must replace
    // the undisplaced desktop to produce a readable lens instead of a double image.
    float center_alpha = lerp(0.065, 0.11, material_opacity);
    // Monitor capture includes Fairy itself. Keep enough real desktop contribution for the next
    // frame's inverse composite to recover the backdrop without quantization-amplified white bars.
    // A 0.88-0.91 rim still replaces almost all undisplaced pixels and preserves strong refraction.
    float edge_alpha = lerp(0.88, 0.91, material_opacity);
    // During a cross-monitor handoff the old surface can briefly lose a valid composite source.
    // Keep that optical shell transparent until the candidate monitor session has a first frame.
    if (capture_source_valid < 0.5) {
        center_alpha = min(center_alpha, 0.012);
        edge_alpha = min(edge_alpha, 0.24);
    }
    // Once pixels are displaced, replace the undisplaced desktop almost completely. A partially
    // transparent displaced sample is perceived as a duplicate image rather than refraction.
    float edge_material = saturate(
        smoothstep(0.02, 0.16, edge_focus) + outer_caustic * 0.12
    );
    float alpha = shape_mask * lerp(center_alpha, edge_alpha, edge_material);
    // The identity beacon is emissive, not part of the glass fill. Give it independent coverage so
    // the optically clear center does not erase the breathing point after premultiplication.
    float beacon_alpha = core_orb
        * (0.52 + state_energy * 0.28 + voice_pulse * 0.16)
        * lerp(0.70, 1.0, pulse);
    alpha = max(alpha, shape_mask * beacon_alpha);
    color = linear_to_srgb(saturate(color));
    return float4(color * alpha, alpha);
}
