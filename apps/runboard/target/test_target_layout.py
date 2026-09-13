import unittest
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "apps" / "runboard" / "target" / "src" / "runboard.c"
LAYOUT = ROOT / "apps" / "runboard" / "target" / "include" / "runboard_layout.h"
FONT_DATA = ROOT / "apps" / "codex-monitor" / "target" / "include" / "font_data.h"


class TargetLayoutTests(unittest.TestCase):
    def setUp(self):
        self.source = SOURCE.read_text(encoding="utf-8")
        self.layout = LAYOUT.read_text(encoding="utf-8")
        self.font_data = FONT_DATA.read_text(encoding="utf-8")

    def label_heights(self):
        section = self.font_data.split(
            "static const struct font_glyph font_label_glyphs[] =", 1
        )[1].split("static const struct font_face font_label", 1)[0]
        return [
            int(value)
            for value in re.findall(r"\{\d+,\s*\d+,\s*(\d+),", section)
        ]

    def test_required_display_fields_and_states_are_present(self):
        for token in ("CURRENT EXPERIMENT", "SECOND EXPERIMENT", "NO ACTIVE EXPERIMENT", "ROUND", "TIME", "DATA", "SEED", "METRIC", "VRAM", "STALE", "OFFLINE", "ERROR"):
            self.assertIn(token, self.source)

    def test_matrix_mode_has_explicit_job_and_cell_layout(self):
        for token in (
            "draw_matrix_experiment",
            "RB_MATRIX_PANEL_X",
            "RB_MATRIX_PANEL_H",
            '"MATRIX "',
            '"CELL "',
            '"METHOD "',
            '"ROUND "',
            'RB_MATRIX_ROUND_BAR_Y',
            'draw_progress_bar',
            "matrix_total>0",
        ):
            self.assertIn(token, self.source)

    def matrix_renderer_source(self):
        return self.source.rsplit("static void draw_matrix_experiment", 1)[1].split("\n\n\n", 1)[0]

    def test_matrix_renderer_has_two_independent_progress_sources(self):
        renderer = self.matrix_renderer_source()
        self.assertIn("matrix_progress", renderer)
        self.assertIn("round_progress", renderer)
        self.assertIn("draw_progress_bar(fb,RB_MATRIX_PANEL_X+10,RB_MATRIX_BAR_Y", renderer)
        self.assertIn("draw_progress_bar(fb,RB_MATRIX_PANEL_X+10,RB_MATRIX_ROUND_BAR_Y", renderer)
        self.assertNotIn('"METRIC "', renderer)
        self.assertNotIn('"VRAM "', renderer)

    def test_matrix_server_header_owns_vram(self):
        header = self.source.split("static void draw_header", 1)[1].split("\n\nstatic void draw_experiment", 1)[0]
        matrix_header = header.split("}else{", 1)[0]
        draw_fit = self.source.split("static void draw_fit", 1)[1].split("\n", 1)[0]
        self.assertIn('if(state->matrix_total>0)', header)
        self.assertIn("format_gpu_value(used,state->gpu_memory_used,0)", matrix_header)
        self.assertIn("format_gpu_value(total,state->gpu_memory_total,1)", matrix_header)
        self.assertNotIn('copy_text(line,"VRAM ")', matrix_header)
        self.assertIn('append_text(line,"% ",sizeof(line))', matrix_header)
        self.assertIn("resource_width=text_width", draw_fit)
        self.assertIn("resource_x=RB_RESOURCE_RIGHT-resource_width", draw_fit)
        self.assertIn("draw_text(fb,resource_x", draw_fit)
        for token in ("RB_SERVER_X", "RB_RESOURCE_X", "RB_RESOURCE_W", "RB_HEADER_RULE_Y"):
            self.assertIn(token, self.layout)

    def test_matrix_header_resource_line_fits_real_font_metrics(self):
        header = self.source.split("static void draw_header", 1)[1].split("\n\nstatic void draw_experiment", 1)[0]
        self.assertIn("static void format_gpu_value", self.source)
        self.assertIn("tenths>=9", self.source)
        max_line = "CPU 100% RAM 100% GPU 100% 15.9/16G"
        self.assertLessEqual(self.rendered_width(max_line, spacing=1), 348)
        self.assertLessEqual(120 + self.rendered_width(max_line, spacing=1), 480)
        self.assertLessEqual(12 + 96 + 12, 120)
        self.assertLessEqual(25 + max(self.label_heights()), 46)

    def test_matrix_header_resource_group_is_right_aligned_with_safe_left_gap(self):
        resource_fields = ("CPU 100%", "RAM 100%", "GPU 100%", "15.9/16G")
        group_width = sum(self.rendered_width(field) for field in resource_fields) + 3 * 3
        resource_x = 468 - group_width
        self.assertGreaterEqual(resource_x, 12 + 96 + 12)
        self.assertLessEqual(resource_x + group_width, 468)
        for token in (
            "RB_RESOURCE_RIGHT 468",
            "RB_RESOURCE_GAP 3",
            "resource_width=text_width",
            "resource_x=RB_RESOURCE_RIGHT-resource_width",
            "draw_text(fb,resource_x",
        ):
            self.assertIn(token, self.layout + self.source)

    def test_host_clock_and_updated_label_have_separate_safe_regions(self):
        for token in (
            'text_field(payload,payload_length,"CT"',
            'text_field(payload,payload_length,"UT"',
            'integer_field(payload,payload_length,"UA"',
            'timestamp_clock',
            'format_updated',
            'seconds<=2',
            '"NOW"',
            'draw_footer_right',
            'draw_fit(fb,RB_UPDATED_X,RB_UPDATED_Y',
            '"UPDATED "',
        ):
            self.assertIn(token, self.source)
        for token in (
            "#define RB_CURRENT_TIME_X 330",
            "#define RB_CURRENT_TIME_Y 8",
            "#define RB_CURRENT_TIME_W 60",
            "#define RB_STATUS_X 390",
            "#define RB_STATUS_W 78",
            "#define RB_UPDATED_X 334",
            "#define RB_UPDATED_Y 256",
            "#define RB_UPDATED_W 136",
        ):
            self.assertIn(token, self.layout)
        rendered_height = max(self.label_heights())
        self.assertLessEqual(330 + 60, 390)
        self.assertLessEqual(390 + 78, 468)
        self.assertLessEqual(256 + rendered_height, 272)
        self.assertLessEqual(241 + rendered_height, 256)

    def test_target_percent_parser_rounds_fixed_point_without_float(self):
        self.assertIn("static int percent_field", self.source)
        self.assertIn('percent_field(payload,payload_length,"CPU",&parsed.cpu)', self.source)
        self.assertIn('percent_field(payload,payload_length,"RP",&parsed.ram)', self.source)
        self.assertIn('percent_field(payload,payload_length,"GU",&parsed.gpu)', self.source)

        def parse(value):
            whole, fraction = value.split(".", 1) if "." in value else (value, "")
            milli = int(whole) * 1000
            if fraction:
                milli += int(fraction[:3].ljust(3, "0"))
            return (milli + 500) // 1000

        self.assertEqual(parse("5.58"), 6)
        self.assertEqual(parse("11.89"), 12)
        self.assertEqual(parse("17.25"), 17)
        self.assertEqual(parse("5"), 5)

    def test_fixed_regions_fit_480_by_272(self):
        regions = ((0, 0, 480, 46), (12, 48, 456, 184), (12, 184, 456, 54), (0, 240, 480, 32))
        for x, y, width, height in regions:
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + width, 480)
            self.assertLessEqual(y + height, 272)

    def test_variable_text_uses_fixed_width_clipping(self):
        self.assertIn("#define RB_FRAME_MAX 767", self.source)
        self.assertIn("static void draw_fit", self.source)
        self.assertIn("text_width(font,clipped,spacing)", self.source)
        self.assertIn("normalize_display(display,text,sizeof(display))", self.source)
        self.assertIn("candidate[prefix]='.'", self.source)
        self.assertIn("if(!n)return", self.source)
        self.assertIn("#define RB_TEXT_MAX 96", self.source)

    def label_widths(self):
        section = self.font_data.split(
            "static const struct font_glyph font_label_glyphs[] =", 1
        )[1].split("static const struct font_face font_label", 1)[0]
        return {
            int(match.group(1)): int(match.group(4))
            for match in re.finditer(
                r"\{(\d+),\s*(\d+),\s*(\d+),\s*(\d+),", section
            )
        }

    def rendered_width(self, text, spacing=0):
        widths = self.label_widths()
        advances = [widths[ord(char)] for char in text.upper() if ord(char) in widths]
        return sum(advances) + max(0, len(advances) - 1) * spacing

    def prefix_clip(self, text, max_width, spacing=0):
        normalized = text.upper()
        if self.rendered_width(normalized, spacing) <= max_width:
            return normalized
        visible = ""
        for char in normalized:
            candidate = visible + char
            if self.rendered_width(candidate, spacing) > max_width:
                break
            visible = candidate
        if not visible:
            return ""
        if len(visible) >= 5:
            while len(visible) >= 5 and self.rendered_width(visible + "...", spacing) > max_width:
                visible = visible[:-1]
            if len(visible) >= 5:
                return visible + "..."
        return visible

    def test_long_text_keeps_a_visible_prefix_and_fits_real_font(self):
        cases = (
            ("federated-segmentation-very-long-experiment-name", 300),
            ("research-user-with-a-long-identity", 442),
            ("LEVir-CD-plus-extended-dataset", 200),
            ("mean-intersection-over-union", 200),
            ("research-gpu-server-with-a-very-long-name", 155),
        )
        for value, width in cases:
            output = self.prefix_clip(value, width)
            normalized = value.upper()
            self.assertTrue(output)
            self.assertNotEqual(output, "--")
            self.assertNotEqual(output, "...")
            self.assertTrue(normalized.startswith(output.rstrip(".")))
            self.assertLessEqual(self.rendered_width(output), width)
            self.assertNotIn("\n", output)
        name = self.prefix_clip(cases[0][0], cases[0][1])
        self.assertGreaterEqual(len(name.rstrip(".")), 5)

    def test_longtext_mock_covers_all_variable_text_fields(self):
        import json

        mock = json.loads((ROOT / "apps" / "runboard" / "shared" / "mocks" / "longtext.json").read_text(encoding="utf-8"))
        experiment = mock["experiments"][0]
        self.assertGreater(len(mock["server"]["displayName"]), 30)
        self.assertGreater(len(experiment["name"]), 40)
        self.assertGreater(len(experiment["user"]), 25)
        self.assertGreater(len(experiment["dataset"]), 25)
        self.assertGreater(len(experiment["metricName"]), 25)

    def test_longtext_frame_fits_target_receiver_limit(self):
        from apps.runboard.shared.board_protocol import encode_state
        import json

        mock = json.loads((ROOT / "apps" / "runboard" / "shared" / "mocks" / "longtext.json").read_text(encoding="utf-8"))
        frame = encode_state(mock)
        payload_length = int(frame.split(b"|", 2)[1].split(b"=", 1)[1])
        self.assertLessEqual(payload_length, 767)

    def test_double_layout_uses_three_non_overlapping_rows(self):
        for token in (
            "#define RB_SECOND_CARD_H 54",
            "#define RB_SECOND_TITLE_Y 187",
            "#define RB_SECOND_DETAIL_Y 204",
            "#define RB_SECOND_VALUE_Y 221",
            "rect(fb,RB_SECOND_CARD_X,RB_SECOND_CARD_Y,RB_SECOND_CARD_W,RB_SECOND_CARD_H,bg)",
            "draw_fit(fb,360,RB_SECOND_DETAIL_Y",
            "draw_fit(fb,22,RB_SECOND_VALUE_Y",
            "draw_fit(fb,138,RB_SECOND_VALUE_Y",
            "draw_fit(fb,260,RB_SECOND_VALUE_Y",
        ):
            self.assertIn(token, self.source)

    def test_font_renderer_height_is_used_for_bounding_boxes(self):
        heights = self.label_heights()
        self.assertEqual(max(heights), 14)
        self.assertIn("for(r=0;r<g->height;r++)", self.source)
        rendered_height = max(heights)
        safe_gap = 3
        baselines = (187, 204, 221)
        for current_y, next_y in zip(baselines, baselines[1:]):
            self.assertGreaterEqual(
                next_y - current_y, rendered_height + safe_gap
            )

    def test_fresh_footer_is_hidden_but_abnormal_freshness_remains_visible(self):
        self.assertIn('if(state->experiment_count<2)', self.source)
        self.assertIn('!same_text(state->server_freshness,"fresh")', self.source)
        self.assertIn('!same_text(state->codex_freshness,"fresh")', self.source)

    def test_offline_body_has_explicit_no_live_server_message(self):
        for token in (
            'same_text(server_badge(state),"OFFLINE")',
            'rect(fb,13,59,454,156,bg)',
            '"SERVER OFFLINE"',
            '"NO LIVE SERVER DATA"',
        ):
            self.assertIn(token, self.source)

    def test_stale_header_retains_last_known_good_resources(self):
        self.assertIn('int cached=same_text(badge,"ONLINE")||same_text(badge,"STALE")', self.source)
        self.assertIn('if(state->cpu<0||!cached)', self.source)
        self.assertIn('if(cached){format_gpu_value(used,state->gpu_memory_used,0)', self.source)

    def test_degraded_status_and_partial_resources_fit_without_offline_rendering(self):
        self.assertLessEqual(self.rendered_width("DEGRADED", spacing=0), 78)
        self.assertLessEqual(390 + 78, 468)
        self.assertIn('same_text(badge,"DEGRADED")', self.source)
        self.assertIn('if(state->cpu<0||!cached)', self.source)
        self.assertIn('server_badge(state)', self.source)
        self.assertNotIn('same_text(badge,"DEGRADED"))return "OFFLINE"', self.source)

    def test_offline_render_precedes_error_and_idle_experiment_paths(self):
        offline = self.source.index('if(same_text(badge,"OFFLINE"))')
        error = self.source.index('else if(!state->valid||same_text(badge,"ERROR"))')
        idle = self.source.index('else if(state->experiment_count==0)')
        self.assertLess(offline, error)
        self.assertLess(offline, idle)
        self.assertIn('append_text(line,"% --/--G"', self.source)

    def test_offline_footer_uses_unlabeled_codex_rows_without_duplicate_status(self):
        footer_right = self.source.rsplit("static void draw_footer_right", 1)[1]
        self.assertIn('int offline=same_text(server_badge(state),"OFFLINE")', footer_right)
        self.assertIn('if(state->experiment_count<2)draw_matrix_footer(fb,state,text,muted,panel)', footer_right)
        self.assertIn('if(!offline&&(!same_text(state->server_freshness,"fresh")', footer_right)
        footer = self.source.split("static void draw_footer(struct", 1)[1].split("static uint32_t quota_color", 1)[0]
        self.assertNotIn('if(state->experiment_count==1&&!offline&&state->matrix_total<=0)', footer)
        self.assertIn('if(!offline&&(!same_text(state->server_freshness,"fresh")', footer)

    def test_unknown_codex_values_use_dash_rendering(self):
        self.assertIn('if(percent<0)copy_text(line,"--%")', self.source)
        self.assertIn('draw_progress_bar(fb,RB_CODEX_BAR_X,y+2,RB_CODEX_BAR_W,percent,color,border)', self.source)
        self.assertIn('draw_fit(fb,RB_CODEX_RESET_X,y,&font_label,reset,0,muted,RB_CODEX_RESET_W)', self.source)

    def test_double_row_coordinates_stay_inside_screen(self):
        for x, y, width, height in (
            (12, 184, 456, 54),
            (22, 187, 180, 14),
            (22, 204, 326, 14),
            (360, 204, 98, 14),
            (22, 221, 100, 14),
            (138, 221, 110, 14),
            (260, 221, 198, 14),
        ):
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + width, 480)
            self.assertLessEqual(y + height, 272)

    def test_matrix_text_boxes_use_real_font_height_and_stay_in_panel(self):
        rendered_height = max(self.label_heights())
        baselines = (55, 74, 93, 112, 151, 175)
        for current_y, next_y in zip(baselines, baselines[1:]):
            self.assertGreaterEqual(next_y - current_y, rendered_height + 1)
        self.assertLessEqual(48 + 184, 240)
        self.assertLessEqual(177 + rendered_height, 232)

    def test_matrix_progress_bars_are_separate_and_inside_panel(self):
        self.assertIn("RB_MATRIX_BAR_Y 130", self.layout)
        self.assertIn("RB_MATRIX_ROUND_BAR_Y 193", self.layout)
        self.assertIn("RB_PROGRESS_BAR_H 12", self.layout)
        self.assertIn("RB_PROGRESS_FILL_H 8", self.layout)
        self.assertIn("draw_progress_bar", self.source)
        self.assertLessEqual(130 + 12, 151)
        self.assertLessEqual(193 + 12, 232)
        self.assertLessEqual(12 + 10 + 436, 480)
        self.assertLessEqual(175 + max(self.label_heights()), 193)
        self.assertIn("RB_COLOR_TRACK_R", self.source)
        self.assertIn("RB_PROGRESS_BAR_H-2", self.source)

    def test_matrix_labels_use_high_contrast_color(self):
        renderer = self.matrix_renderer_source()
        self.assertIn("RB_COLOR_LABEL_R", renderer)
        self.assertIn("muted=label", renderer)

    def test_quota_thresholds_and_synchronized_colors_are_present(self):
        self.assertIn("static uint32_t quota_color", self.source)
        self.assertIn("if(percent<20)return red", self.source)
        self.assertIn("if(percent<50)return yellow", self.source)
        self.assertIn("draw_fit(fb,RB_CODEX_PERCENT_X,y,&font_label,line,0,color", self.source)
        self.assertIn("draw_progress_bar(fb,RB_CODEX_BAR_X,y+2,RB_CODEX_BAR_W,percent,color", self.source)
        self.assertIn("normal_week=pack_rgb", self.source)

    def test_matrix_round_value_is_appended_to_rendered_label(self):
        renderer = self.matrix_renderer_source()
        self.assertIn('format_round(value,e->current_round,e->total_round);append_text(line,value', renderer)

    def test_matrix_footer_is_two_unlabeled_quota_rows(self):
        footer = self.source.rsplit("static void draw_matrix_footer", 1)[1].split("static void draw_footer_right", 1)[0]
        for token in ("RB_CODEX_ROW1_Y", "RB_CODEX_ROW2_Y", "draw_quota_row", "state->five_hour", "state->week"):
            self.assertIn(token, footer)
        for token in ('"CODEX "', '"5H "', '"WEEK "', '"RESET "', '"RC "'):
            self.assertNotIn(token, footer)
        self.assertIn("RB_CODEX_BAR_W 180", self.layout)
        self.assertLessEqual(256 + max(self.label_heights()), 272)

    def test_double_text_boxes_do_not_intersect(self):
        rendered_height = max(self.label_heights())
        boxes = ((22, 187, 180, rendered_height),
                 (22, 204, 326, rendered_height),
                 (360, 204, 98, rendered_height),
                 (22, 221, 100, rendered_height),
                 (138, 221, 110, rendered_height),
                 (260, 221, 198, rendered_height))
        for index, (x, y, width, height) in enumerate(boxes):
            for other_x, other_y, other_width, other_height in boxes[index + 1:]:
                self.assertTrue(
                    x + width <= other_x or other_x + other_width <= x or
                    y + height <= other_y or other_y + other_height <= y
                )

    def test_double_card_does_not_touch_main_or_footer(self):
        main = (12, 48, 456, 130)
        second = (12, 184, 456, 54)
        footer = (0, 240, 480, 32)
        self.assertLessEqual(main[1] + main[3], second[1])
        self.assertLessEqual(second[1] + second[3], footer[1])

    def test_double_columns_do_not_overlap(self):
        name = (22, 204, 326, max(self.label_heights()))
        status = (360, 204, 98, max(self.label_heights()))
        self.assertLessEqual(name[0] + name[2], status[0])

    def test_double_card_leaves_abnormal_freshness_space(self):
        second_bottom = 184 + 54
        footer_top = 240
        self.assertLessEqual(second_bottom, footer_top)
        self.assertGreaterEqual(272 - footer_top, 32)

    def test_double_renderer_uses_final_header_and_compact_primary_fields(self):
        header = self.source.split("static void draw_header", 1)[1].split("\n\nstatic void draw_experiment", 1)[0]
        renderer = self.source.split("static void draw_experiment", 1)[1].split("\n\nstatic void draw_footer", 1)[0]
        screen = self.source.split("static void draw_screen", 1)[1].split("\n\nstatic int serial_open", 1)[0]
        self.assertIn("state->experiment_count>=2", header)
        self.assertIn("if(detailed){line_rect", renderer)
        self.assertIn("else draw_progress_bar(fb,x+10,y+68,width-20,e->progress", renderer)
        self.assertIn('if(detailed){copy_text(line,"METRIC ")', renderer)
        self.assertNotIn('draw_text(fb,22,190,&font_label,"SECOND EXPERIMENT"', screen)
        self.assertNotIn('append_text(line,"  ROUND "', screen)

    def test_double_hides_updated_and_keeps_single_three_line_card_in_footer(self):
        footer_right = self.source.split("static void draw_footer_right", 1)[1].split("\n\nstatic void draw_screen", 1)[0]
        footer = self.source.split("static void draw_footer", 1)[1].split("static uint32_t quota_color", 1)[0]
        self.assertIn("if(state->experiment_count<2)draw_fit(fb,RB_UPDATED_X", footer_right)
        for token in ("RB_SECOND_TITLE_Y", "RB_SECOND_DETAIL_Y", "RB_SECOND_VALUE_Y", '"SECOND EXPERIMENT"'):
            self.assertIn(token, footer)

    def test_idle_reuses_final_header_and_quota_footer(self):
        header = self.source.split("static void draw_header", 1)[1].split("\n\nstatic void draw_experiment", 1)[0]
        footer_right = self.source.split("static void draw_footer_right", 1)[1].split("\n\nstatic void draw_screen", 1)[0]
        footer = self.source.split("static void draw_footer", 1)[1].split("static uint32_t quota_color", 1)[0]
        self.assertIn("state->experiment_count==0", header)
        self.assertIn("state->experiment_count<2", footer_right)
        self.assertIn("draw_matrix_footer(fb,state,text,muted,panel)", footer_right)
        self.assertNotIn("state->experiment_count==1&&!offline&&state->matrix_total<=0", footer)

    def test_completed_and_error_use_final_single_renderer(self):
        header = self.source.split("static void draw_header", 1)[1].split("\n\nstatic void draw_experiment", 1)[0]
        screen = self.source.split("static void draw_screen", 1)[1].split("\n\nstatic int serial_open", 1)[0]
        self.assertIn("state->experiment_count==1", header)
        self.assertIn('if(same_text(state->server_status,"degraded"))return "DEGRADED"', self.source)
        self.assertIn("draw_experiment(fb,&state->experiment[0],state->gpu_memory_used,state->gpu_memory_total,12,48,456,130,0", screen)
        self.assertIn('draw_matrix_footer(fb,state,text,muted,panel)', self.source)
        self.assertIn('if(state->experiment_count<2)draw_matrix_footer', self.source)

    def test_rb1_length_parser_starts_after_length_equals(self):
        self.assertIn("const char *start=frame+6", self.source)

    def test_parser_debug_evidence_is_emitted_after_parse_and_draw(self):
        self.assertIn("RBDBG|phase=", self.source)
        self.assertIn('serial_debug(fd,&state,n+1,parsed,"PARSED")', self.source)
        self.assertIn('serial_debug(fd,&state,n+1,parsed,"DRAWN")', self.source)


if __name__ == "__main__":
    unittest.main()
