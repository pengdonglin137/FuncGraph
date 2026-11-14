"""Inline canvas styling preserves truthful extents and bounded paint work."""

from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class InlineSliceDrawingTest(unittest.TestCase):
    def check_drawing(self, assertions):
        source = (ROOT / 'assets/timeline/render.js').read_text()
        fonts = source[source.index('const FONT ='):source.index('let charW =')]
        palette = source[source.index('function palette()'):source.index('function makeHatch(')]
        style = source[source.index('function sliceStyle('):source.index('let matchingCache =')]
        drawing = source[source.index('function drawSlice('):source.index('function drawOff(')]
        setup = '''
            const assert = require('node:assert/strict');
            let theme = 'light', dark = false, P = null, hatch = null, offHatch = null;
            const document = {documentElement:{getAttribute:() => theme}};
            const colorCache = new Map();
            function updateHeatLegend() {}
            function makeHatch(color) { return color; }
            const N = 2, W = 300;
            const FL = {BOUNDARY:1 << 26, IRQOFF:4, TRUNC:1 << 16,
                ORPHAN:1 << 17, MISMATCH:1 << 19, RS_SHIFT:4};
            const sFlags = [0, 0, 1 << 23, 1 << 24];
            const eRole = [-1, -1, -1, -1], eFate = [1, 1, 1, 1];
            const eChain = [-1, -1, -1, -1], eErr = [2, 2, 2, 2], CHAINS = [];
            const ov = {err:false, irq:false, preempt:false, mk:false, rs:false};
            let showMinor = true, colorMode = 'ctx', sel = -1, hov = -1, dimmed = false;
            const charW = 6.5;
            function isGhost(index) { return index === 3; }
            function isDim() { return dimmed; }
            function baseColor() { return 'recorded-' + colorMode; }
            function sliceLabel(index) { return ['root', 'child', 'helper_inline', 'gap'][index]; }
            function errName() { return 'ENOENT'; }
            const operations = [];
            const ctx = {
                globalAlpha:1, font:FONT, dash:[], path:[],
                fillRect(...bounds) {
                    operations.push({kind:'fill', bounds, color:this.fillStyle, alpha:this.globalAlpha});
                },
                strokeRect(...bounds) {
                    operations.push({kind:'outline', bounds, color:this.strokeStyle,
                        width:this.lineWidth, dash:this.dash.slice(), alpha:this.globalAlpha});
                },
                setLineDash(pattern) { this.dash = pattern.slice(); },
                beginPath() { this.path = []; },
                moveTo(...point) { this.path.push({kind:'move', point}); },
                lineTo(...point) { this.path.push({kind:'line', point}); },
                stroke() {
                    operations.push({kind:'path', path:this.path.slice(), color:this.strokeStyle,
                        width:this.lineWidth, dash:this.dash.slice(), alpha:this.globalAlpha});
                },
                fillText(text, ...point) {
                    operations.push({kind:'text', text, point, color:this.fillStyle,
                        font:this.font, alpha:this.globalAlpha});
                },
                measureText() { throw new Error('Do not measure text for every slice'); }
            };
            function paint(index = 2, left = 20, right = 220, labels = true, height = 16) {
                operations.length = 0;
                drawSlice(index, left, right, 20, height, labels);
                return operations;
            }
            palette();
        '''
        result = subprocess.run(['node', '-e', fonts + setup + palette + style + drawing + assertions],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_neutral_palette_and_hover_are_independent_of_measured_duration_colors(self):
        self.check_drawing('''
            const fills = [];
            for (theme of ['light', 'dark']) {
                palette();
                for (colorMode of ['ctx', 'name', 'cpu', 'module', 'total', 'heat']) {
                    hov = -1;
                    assert.deepEqual(sliceStyle(2), [P.inlineFill, P.textDim, P.inlineEdge]);
                    hov = 2;
                    assert.equal(sliceStyle(2)[0], P.inlineHover);
                    assert.notEqual(P.inlineFill, P.inlineHover);
                    assert.ok(P.inlineFill.startsWith('rgba('));
                }
                fills.push(P.inlineFill);
            }
            assert.notEqual(fills[0], fills[1]);
        ''')

    def test_inline_has_only_vertical_dashed_end_caps(self):
        self.check_drawing('''
            const painted = paint();
            assert.equal(painted.filter(operation => operation.kind === 'outline').length, 0);
            const caps = painted.filter(operation => operation.kind === 'path');
            assert.equal(caps.length, 1);
            assert.deepEqual(caps[0].dash, [2, 3]);
            assert.equal(caps[0].width, 1);
            assert.deepEqual(caps[0].path, [
                {kind:'move', point:[20.5, 22]}, {kind:'line', point:[20.5, 34]},
                {kind:'move', point:[218.5, 22]}, {kind:'line', point:[218.5, 34]}
            ]);
            assert.deepEqual(ctx.dash, []);
        ''')

    def test_clipping_never_invents_endpoints_at_viewport_edges(self):
        self.check_drawing('''
            for (const [left, right, expected] of [[-50, 220, 218.5], [20, 350, 20.5]]) {
                const caps = paint(2, left, right).find(operation => operation.kind === 'path');
                assert.equal(caps.path.length, 2);
                assert.equal(caps.path[0].point[0], expected);
                assert.equal(caps.path[1].point[0], expected);
            }
            assert.equal(paint(2, -50, 350).filter(operation => operation.kind === 'path').length, 0);
        ''')

    def test_small_tag_restores_font_and_narrow_slices_prioritize_function_names(self):
        self.check_drawing('''
            const wide = paint().filter(operation => operation.kind === 'text');
            assert.deepEqual(wide.map(operation => operation.text), ['inl', 'helper_inline']);
            assert.equal(wide[0].font, FONT_INLINE);
            assert.equal(wide[0].color, P.inlineTag);
            assert.equal(wide[1].font, FONT);
            assert.equal(wide[1].point[0] - wide[0].point[0], 24);
            assert.equal(ctx.font, FONT);
            const narrow = paint(2, 20, 80).filter(operation => operation.kind === 'text');
            assert.equal(narrow.length, 1);
            assert.equal(narrow[0].text, 'helper_…');
            assert.equal(narrow[0].point[0], 24);
            assert.equal(narrow[0].font, FONT);
        ''')

    def test_thin_zero_width_and_collapsed_frames_do_not_gain_tags_or_invalid_outlines(self):
        self.check_drawing('''
            for (const width of [0, 0.5, 1, 2, 3]) {
                const painted = paint(2, 20, 20 + width);
                assert.equal(painted.length, 1);
                assert.equal(painted[0].kind, 'fill');
                assert.ok(painted[0].bounds[2] >= 0);
            }
            assert.equal(paint(2, 20, 220, false).filter(operation => operation.kind === 'text').length, 0);
            assert.equal(paint(2, 20, 220, false, 3).length, 1);
        ''')

    def test_untraced_frames_retain_full_outline_and_explicit_label(self):
        self.check_drawing('''
            for (theme of ['light', 'dark']) {
                palette();
                const expected = dark ? 'rgba(120,130,150,.22)' : 'rgba(110,120,140,.13)';
                assert.equal(sliceStyle(3)[0], expected);
                const painted = paint(3);
                assert.equal(painted.find(operation => operation.kind === 'text').text, 'untraced gap');
                assert.deepEqual(painted.find(operation => operation.kind === 'outline').dash, [3, 3]);
                assert.equal(painted.filter(operation => operation.kind === 'path').length, 0);
                assert.deepEqual(ctx.dash, []);
            }
        ''')

    def test_hover_selection_and_filter_dimming_preserve_canvas_state(self):
        self.check_drawing('''
            hov = 2;
            let painted = paint();
            assert.equal(painted[0].color, P.inlineHover);
            assert.equal(painted.find(operation => operation.kind === 'outline').color, P.hov);
            sel = 2;
            dimmed = true;
            painted = paint();
            assert.equal(painted[0].alpha, 0.18);
            const selection = painted.find(operation => operation.kind === 'outline');
            assert.equal(selection.color, P.sel);
            assert.equal(selection.width, 2);
            assert.equal(selection.alpha, 1);
            assert.deepEqual(selection.dash, []);
            assert.equal(ctx.globalAlpha, 1);
            assert.equal(ctx.font, FONT);
        ''')

    def test_recorded_call_colors_error_overrides_and_labels_do_not_change(self):
        self.check_drawing('''
            for (colorMode of ['ctx', 'name', 'cpu', 'module', 'total', 'heat']) {
                const painted = paint(0);
                assert.equal(painted[0].color, 'recorded-' + colorMode);
                assert.deepEqual(painted.filter(operation => operation.kind === 'text')
                    .map(operation => operation.text), ['root']);
                assert.equal(painted.filter(operation => operation.kind === 'path').length, 0);
            }
            ov.err = true;
            eRole[0] = 0;
            const painted = paint(0);
            assert.equal(painted[0].color, P.errO);
            assert.equal(painted.find(operation => operation.kind === 'text').text, 'root = ENOENT');
        ''')

    def test_repeated_paint_avoids_per_slice_measurement_and_restores_state(self):
        self.check_drawing('''
            for (let iteration = 0; iteration < 20000; iteration++) {
                paint(2, 20, iteration % 2 ? 80 : 220);
                assert.equal(ctx.font, FONT);
                assert.equal(ctx.globalAlpha, 1);
                assert.deepEqual(ctx.dash, []);
            }
        ''')
