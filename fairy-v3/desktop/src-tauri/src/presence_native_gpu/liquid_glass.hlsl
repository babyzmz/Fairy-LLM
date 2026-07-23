// The desktop texture comes from DXGI Desktop Duplication after every Presence HWND has been
// excluded from DDA. Each output pixel samples one continuous optical field; there are no stacked
// backdrop copies or concentric magnification layers. HostBackdrop remains an identity fallback.
static const float EDGE_MATERIAL_DEPTH_PX = 34.0;
static const float EDGE_LENS_START = 0.40;
static const float EDGE_REFRACTION_MAX_PX = 10.5;
static const float EDGE_DISPERSION_MAX_PX = 1.8;

Texture2D<float4> desktop_texture : register(t0);
SamplerState desktop_sampler : register(s0);

cbuffer PresenceConstants : register(b0) {
    float2 output_size;
    float2 configured_core_center_px;
    float2 configured_capsule_center_px;
    float elapsed_seconds;
    float surface_scale;
    float shape_droplet;
    float shape_bridge;
    float shape_capsule;
    float expansion_direction;
    float activation_style;
    float material_opacity;
    float voice_level;
    float reduced_motion;
    float reduced_transparency;
    float increased_contrast;
    float particles_enabled;
    float diagnostic_solid;
    float capsule_half_width;
    float state_elapsed_seconds;
    float drag_active;
    float state_energy;
    float state_pulse;
    float state_notify_wave;
    float state_voice_mix;
    float state_activation;
    float3 state_accent;
    float state_transition_progress;
    float2 drag_direction;
    float drag_stretch;
    float drag_release;
    float2 surface_origin_px;
    float2 monitor_origin_px;
    float2 monitor_size_px;
    float desktop_texture_active;
    float source_color_mode;
    float source_rotation;
    float3 desktop_padding;
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

float3 linear_to_srgb(float3 color) {
    float3 low = color * 12.92;
    float3 high = 1.055 * pow(max(color, 0.0), 1.0 / 2.4) - 0.055;
    return lerp(low, high, step(0.0031308, color));
}

float3 pq_to_linear(float3 encoded) {
    static const float m1 = 0.1593017578125;
    static const float m2 = 78.84375;
    static const float c1 = 0.8359375;
    static const float c2 = 18.8515625;
    static const float c3 = 18.6875;
    float3 powered = pow(saturate(encoded), 1.0 / m2);
    float3 numerator = max(powered - c1, 0.0);
    float3 denominator = max(c2 - c3 * powered, 0.00001);
    return pow(numerator / denominator, 1.0 / m1);
}

float3 decode_desktop_color(float3 encoded) {
    if (source_color_mode < 0.5) {
        return saturate(encoded);
    }
    if (source_color_mode < 1.5) {
        // RGB10A2 duplication surfaces use the output's PQ transfer function. Map absolute HDR
        // luminance into a bounded SDR presentation without altering the sampled geometry.
        float3 relative_luminance = pq_to_linear(encoded) * 100.0;
        return linear_to_srgb(1.0 - exp(-relative_luminance));
    }
    // RGBA16F duplication surfaces are linear scRGB. Preserve SDR white while rolling off HDR.
    float3 linear_color = max(encoded, 0.0);
    linear_color = linear_color / (1.0 + max(linear_color - 1.0, 0.0) * 0.55);
    return linear_to_srgb(saturate(linear_color));
}

float2 rotate_desktop_uv(float2 uv) {
    if (source_rotation < 1.5) return uv;
    if (source_rotation < 2.5) return float2(uv.y, 1.0 - uv.x);
    if (source_rotation < 3.5) return 1.0 - uv;
    return float2(1.0 - uv.y, uv.x);
}

float2 desktop_uv_from_screen(float2 screen_px) {
    float2 monitor_extent = max(monitor_size_px, 1.0.xx);
    float2 output_uv = (screen_px - monitor_origin_px) / monitor_extent;
    return saturate(rotate_desktop_uv(output_uv));
}

float3 sample_desktop(float2 screen_px) {
    return decode_desktop_color(
        desktop_texture.SampleLevel(desktop_sampler, desktop_uv_from_screen(screen_px), 0.0).rgb
    );
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
    float2 delta = position_px - core_center_px();
    float2 axis = length(drag_direction) > 0.01 ? normalize(drag_direction) : float2(1.0, 0.0);
    float2 normal_axis = float2(-axis.y, axis.x);
    float deformation = drag_stretch * 0.055 + drag_release * 0.026;
    // Move the deformed volume slightly behind its anchor: the leading edge compresses while the
    // trailing edge retains more mass, instead of reading as a uniformly scaled ellipse.
    float drag_center_shift = (drag_stretch * 3.8 + drag_release * 1.4) * surface_scale;
    delta += axis * drag_center_shift;
    float along = dot(delta, axis) / max(1.0 + deformation, 0.88);
    float across = dot(delta, normal_axis) / max(1.0 - deformation * 0.45, 0.88);
    float breathing = reduced_motion > 0.5 ? 0.0 : (state_pulse - 0.82) * 1.2 * surface_scale;
    return length(float2(along, across)) - (72.0 * surface_scale + breathing);
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
    float result = core;
    if (droplet_morph >= 0.001) {
        result = smooth_union(core, droplet, 11.0 * surface_scale);
    }

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

float optical_radius(float2 position_px) {
    // The stable core has a 72 px optical depth. Transitional droplets and bridges are thinner;
    // treating them as a second full lens would create the concentric copies this path replaces.
    float scale = max(surface_scale, 0.01);
    float core_membership = smoothstep(8.0 * scale, -8.0 * scale, core_sdf(position_px));
    return lerp(24.0, 72.0, core_membership) * scale;
}

float optical_radial_progress(float signed_distance, float2 position_px) {
    return saturate(1.0 + signed_distance / max(optical_radius(position_px), 1.0));
}

float smootherstep_range(float edge_start, float edge_end, float value) {
    float progress = saturate((value - edge_start) / max(edge_end - edge_start, 0.0001));
    return progress * progress * progress
        * (progress * (progress * 6.0 - 15.0) + 10.0);
}

float fluid_activation_value() {
    return saturate(activation_style * state_activation);
}

float closed_harmonic(float angle, float density, float phase) {
    float safe_density = max(density, 1.0);
    float lower = floor(safe_density);
    float blend = smoothstep(0.0, 1.0, frac(safe_density));
    return lerp(
        sin(angle * lower + phase),
        sin(angle * (lower + 1.0) + phase),
        blend
    );
}

float fluid_surface_wave(float2 local_px, float radial_progress) {
    float activation = fluid_activation_value();
    if (activation <= 0.0001) return 0.0;
    float scale = max(surface_scale, 0.01);
    float2 core_delta = (local_px - core_center_px()) / scale;
    float angle = atan2(core_delta.y, core_delta.x);
    float time = reduced_motion > 0.5 ? 0.0 : state_elapsed_seconds;
    float primary = closed_harmonic(angle, 3.35, -time * 1.05);
    float counter = closed_harmonic(angle, 5.20, time * 0.62 + 1.7);
    float edge_response = smootherstep_range(0.58, 0.96, radial_progress);
    return (primary * 0.68 + counter * 0.32) * edge_response * activation;
}

float continuous_refraction_px(float radial_progress) {
    // Preserve the center, then bend a wider shoulder into a pronounced outer lens. The C2 ramps
    // overlap into one monotonic coordinate field; they are weights, not separately sampled rings.
    float shoulder = smootherstep_range(EDGE_LENS_START, 0.64, radial_progress);
    float body = smootherstep_range(0.56, 0.86, radial_progress);
    float rim = smootherstep_range(0.74, 1.0, radial_progress);
    float profile = 0.10 * shoulder + 0.48 * body + 0.42 * rim;
    return EDGE_REFRACTION_MAX_PX * profile;
}

float3 sample_continuous_liquid_glass(
    float2 local_px,
    float signed_distance,
    float2 normal
) {
    float radial_progress = optical_radial_progress(signed_distance, local_px);
    float optical_depth_px = optical_radius(local_px);
    float thin_shape_mix = saturate(
        (optical_depth_px - 24.0 * surface_scale) / max(48.0 * surface_scale, 1.0)
    );
    // Thin droplets and capsules scale the same profile down to keep the source mapping monotonic.
    float shape_refraction_scale = lerp(0.55, 1.0, thin_shape_mix);
    float displacement_px = continuous_refraction_px(radial_progress)
        * shape_refraction_scale
        * surface_scale;
    float fluid_wave = fluid_surface_wave(local_px, radial_progress);
    float activation = fluid_activation_value();
    displacement_px *= 1.0 + activation * 0.06 + fluid_wave * 0.025;
    float2 tangent = float2(-normal.y, normal.x);
    float tangential_response_px = fluid_wave * 0.34 * surface_scale;
    float2 base_screen_px = surface_origin_px + local_px
        - normal * displacement_px
        + tangent * tangential_response_px;
    float dispersion_px = smootherstep_range(0.86, 1.0, radial_progress)
        * EDGE_DISPERSION_MAX_PX
        * shape_refraction_scale
        * surface_scale
        * (1.0 + activation * 0.08);
    float3 center = sample_desktop(base_screen_px);
    float3 refracted = float3(
        sample_desktop(base_screen_px - normal * dispersion_px).r,
        center.g,
        sample_desktop(base_screen_px + normal * dispersion_px).b
    );

    // A sub-pixel four-tap Kawase kernel softens only the outermost caustic. Its contribution is
    // deliberately bounded so text remains a single image instead of becoming a blurred copy.
    float blur_mix = smoothstep(0.90, 1.0, radial_progress) * 0.045;
    float kernel_radius = 0.45 * surface_scale;
    float3 kawase = (
        sample_desktop(base_screen_px + normal * kernel_radius)
        + sample_desktop(base_screen_px - normal * kernel_radius)
        + sample_desktop(base_screen_px + tangent * kernel_radius)
        + sample_desktop(base_screen_px - tangent * kernel_radius)
    ) * 0.25;
    return lerp(refracted, kawase, blur_mix);
}

float edge_material_profile(float signed_distance) {
    float scale = max(surface_scale, 0.01);
    float inward_distance = max(-signed_distance, 0.0);
    float rim = saturate(1.0 - inward_distance / max(EDGE_MATERIAL_DEPTH_PX * scale, 1.0));
    return rim * rim * (3.0 - 2.0 * rim);
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
    float density = saturate(0.18 + state_energy * 0.50);
    if (hash21(cell + 31.7) > density) return 0.0;
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

    float2 normal = surface_normal(local_px);
    float3 desktop_color = 0.0.xxx;
    if (desktop_texture_active > 0.5) {
        desktop_color = sample_continuous_liquid_glass(local_px, signed_distance, normal);
    }
    float edge_focus = edge_material_profile(signed_distance);
    float3 material_base = float3(0.91, 0.96, 0.98);

    float rim_fresnel = pow(edge_focus, 1.72);
    float silhouette = exp(-abs(signed_distance) / max(1.35 * scale, 0.75));
    float rim_caustic = exp(
        -abs(signed_distance + 3.2 * scale) / max(2.2 * scale, 0.9)
    );
    float key_highlight = pow(
        saturate(dot(normalize(float2(-0.72, -0.69)), -normal)),
        20.0
    ) * silhouette;
    float fill_highlight = pow(
        saturate(dot(normalize(float2(0.68, 0.73)), -normal)),
        30.0
    ) * silhouette;

    float state_energy = state_energy_value();
    float3 accent = state_accent_color();
    float3 color = material_base;
    if (reduced_transparency > 0.5) {
        color = lerp(color, 0.94.xxx, 0.16);
    }
    color += float3(1.0, 0.995, 0.98) * key_highlight * 0.58;
    color += float3(0.78, 0.92, 1.0) * fill_highlight * 0.28;
    color += float3(0.82, 0.96, 1.0) * rim_caustic * 0.12;
    color += float3(0.82, 0.94, 1.0) * rim_fresnel * silhouette * 0.15;
    float state_travel = 0.5 + 0.5 * sin(
        dot(local_px - core_center_px(), normalize(float2(0.94, -0.34)))
            * 0.055 / scale
        - state_elapsed_seconds * (1.4 + state_energy * 8.0)
    );
    float state_flow = pow(saturate(state_travel), 10.0)
        * rim_caustic
        * state_energy
        * (1.0 - reduced_motion);
    color += accent * state_flow * 0.14;
    float drag_energy = drag_stretch * (1.0 - reduced_motion);
    float drag_light = pow(saturate(dot(-normal, normalize(drag_direction + 0.0001))), 12.0);
    color += float3(0.84, 0.97, 1.0) * drag_light * silhouette * drag_energy * 0.18;
    if (increased_contrast > 0.5) {
        color = lerp(color, 1.0.xxx, saturate(silhouette * 0.34));
    }

    float core_radius = length(local_px - core_center_px()) / scale;
    float pulse = state_pulse_value();
    float voice_pulse = state_voice_mix;
    float notify_wave = notify_wave_value();
    float pulse_progress = saturate((pulse - 0.64) / 0.36);
    float ring_offset = (pulse_progress - 0.5) * 1.8 + notify_wave * 3.2;
    float atmosphere_outer = exp(-abs(core_radius - (36.0 + ring_offset)) / 0.92);
    float atmosphere_inner = exp(-abs(core_radius - (24.0 - ring_offset * 0.28)) / 0.82);
    float atmosphere_outer_halo = exp(
        -abs(core_radius - (36.0 + ring_offset)) / 3.4
    );
    float atmosphere_inner_halo = exp(
        -abs(core_radius - (24.0 - ring_offset * 0.28)) / 2.8
    );
    float atmosphere_alpha = (
        atmosphere_outer * (0.15 + state_energy * 0.12)
        + atmosphere_inner * (0.11 + state_energy * 0.10)
    ) * lerp(0.82, 1.08, pulse_progress);
    // Fairy's two atmosphere rings and breathing beacon are an independent foreground identity
    // layer. No broad center fill, glow, desktop sample, dispersion, or caustic is allowed here.
    float backdrop_luminance = dot(
        saturate(desktop_color),
        float3(0.2126, 0.7152, 0.0722)
    );
    float light_backdrop = smootherstep_range(0.58, 0.92, backdrop_luminance);
    float3 identity_on_dark = float3(0.60, 0.90, 1.0);
    float3 identity_on_light = float3(0.025, 0.34, 0.92);
    float3 identity_color = lerp(identity_on_dark, identity_on_light, light_backdrop);
    identity_color = lerp(
        identity_color,
        accent,
        saturate(state_energy * 1.25 + voice_pulse * 0.22) * 0.22
    );
    float beacon_core = exp(-(core_radius * core_radius) / 10.5);
    float beacon_halo = exp(-(core_radius * core_radius) / 68.0);
    float beacon_emission = (
        beacon_core * (0.66 + state_energy * 0.30 + voice_pulse * 0.16)
        + beacon_halo * (0.14 + state_energy * 0.12 + voice_pulse * 0.08)
    ) * lerp(0.80, 1.16, pulse_progress);
    float identity_contrast_alpha = saturate(
        (
            atmosphere_outer_halo * 0.13
            + atmosphere_inner_halo * 0.10
            + beacon_halo * 0.12
        ) * lerp(0.18, 1.0, light_backdrop)
    );
    float3 identity_contrast_color = lerp(
        float3(0.02, 0.12, 0.22),
        float3(0.008, 0.045, 0.16),
        light_backdrop
    );

    float shape_mask = saturate((1.5 * scale - signed_distance) / (2.5 * scale));
    // The center remains one identity desktop sample. Material opacity grows only toward the edge;
    // this layer never replaces the sampled desktop with a dark or opaque center fill.
    float center_alpha = reduced_transparency > 0.5
        ? lerp(0.08, 0.13, material_opacity)
        : 0.0;
    float edge_alpha = lerp(0.08, 0.15, material_opacity);
    float edge_material = pow(saturate(edge_focus), 1.65);
    float alpha = shape_mask * lerp(center_alpha, edge_alpha, edge_material);
    float optical_highlight_alpha = saturate(
        key_highlight * 0.48
        + fill_highlight * 0.24
        + rim_caustic * 0.12
        + silhouette * 0.07
    );
    alpha = max(alpha, shape_mask * optical_highlight_alpha);
    float atmosphere_layer_alpha = saturate(atmosphere_alpha * 1.55) * shape_mask;
    float beacon_layer_alpha = saturate(beacon_emission) * shape_mask;
    float contrast_layer_alpha = identity_contrast_alpha * shape_mask;
    float3 identity_premultiplied = linear_to_srgb(identity_contrast_color)
        * contrast_layer_alpha;
    float identity_alpha = contrast_layer_alpha;
    identity_premultiplied = linear_to_srgb(identity_color)
        * atmosphere_layer_alpha
        + identity_premultiplied * (1.0 - atmosphere_layer_alpha);
    identity_alpha = atmosphere_layer_alpha
        + identity_alpha * (1.0 - atmosphere_layer_alpha);
    float3 beacon_color = lerp(
        float3(0.96, 0.985, 1.0),
        float3(0.30, 0.78, 1.0),
        light_backdrop
    );
    identity_premultiplied = linear_to_srgb(beacon_color)
        * beacon_layer_alpha
        + identity_premultiplied * (1.0 - beacon_layer_alpha);
    identity_alpha = beacon_layer_alpha
        + identity_alpha * (1.0 - beacon_layer_alpha);
    float material_alpha = alpha;
    float3 material_premultiplied = linear_to_srgb(saturate(color)) * material_alpha;
    float3 foreground_premultiplied = identity_premultiplied
        + material_premultiplied * (1.0 - identity_alpha);
    float foreground_alpha = identity_alpha
        + material_alpha * (1.0 - identity_alpha);
    float desktop_alpha = shape_mask * saturate(desktop_texture_active);
    float3 output_premultiplied = foreground_premultiplied
        + desktop_color * desktop_alpha * (1.0 - foreground_alpha);
    float output_alpha = foreground_alpha
        + desktop_alpha * (1.0 - foreground_alpha);
    return float4(output_premultiplied, output_alpha);
}
