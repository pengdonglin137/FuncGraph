from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class TimelinePositionTest(unittest.TestCase):
    def run_position(self, assertions):
        path = Path(__file__).resolve().parents[1] / 'assets' / 'timeline' / 'panels.js'
        text = path.read_text()
        helper = text[text.index('function safeSourceUrl('):text.index('function callNavigationHtml(')]
        position = text[text.index('function callSitePos('):text.index('function fillCallSite(')]
        setup = '''
            const assert = require('node:assert/strict');
            const csPosCache = new Map();
            const D = {sources:{callers:{}}};
            let sCaller = [0, 1, 2, 3];
            const isSyn = index => index >= 4;
            let legacyReads = 0;
            const callSiteBlock = () => { legacyReads++; throw new Error('Source line map read'); };
            const fgSourceHtml = () => { throw new Error('Source pool read'); };
            const document = {baseURI:'https://report.test/report.html',
                createElement() {throw new Error('Source DOM parsed');}};
        '''
        result = subprocess.run(['node', '-e', setup + helper + position + assertions],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_innermost_source_and_zero_caller_index_need_no_source_html(self):
        self.run_position('''
            const chain = [['outer', 'kernel/outer.c', 12, 'https://kernel.test/outer#L12'],
                ['inner', 'include/inner.h', 27, 'https://kernel.test/inner#L27']];
            chain.forEach(Object.freeze);
            Object.freeze(chain);
            D.sources.callers[0] = chain;
            const original = JSON.stringify(chain);
            assert.deepEqual(callSitePos(0), {func:'inner', at:'include/inner.h:27',
                href:'https://kernel.test/inner#L27', inl:true});
            assert.equal(callSitePos(0), callSitePos(0));
            assert.equal(JSON.stringify(chain), original);
            assert.equal(legacyReads, 0);
        ''')

    def test_explicit_missing_source_is_not_unknown_legacy_metadata(self):
        self.run_position('''
            D.sources.callers[0] = [];
            assert.equal(callSitePos(0), null);
            assert.equal(callSitePos(4), null);
            assert.equal(legacyReads, 0);
            assert.throws(() => callSitePos(1), /Source line map read/);
            delete D.sources;
            assert.throws(() => callSitePos(0), /Source line map read/);
            assert.equal(legacyReads, 2);
        ''')

    def test_single_frame_optional_links_and_unsafe_url_handling(self):
        self.run_position('''
            for (const [href, expected] of [['', null], ['javascript:alert(1)', null],
                ['vscode://file/tree/kernel.c:8', 'vscode://file/tree/kernel.c:8']]) {
                D.sources.callers[0] = [['worker', '<unsafe>.c', 8, href]];
                assert.deepEqual(callSitePos(0), {func:'worker', at:'<unsafe>.c:8', href:expected, inl:false});
            }
            assert.equal(legacyReads, 0);
        ''')

    def test_partial_or_malformed_compact_metadata_retains_the_legacy_path(self):
        self.run_position('''
            for (const chain of [null, {}, [null], [['fn']], [['fn', 'file.c', 0]],
                [['fn', 'file.c', -1]], [['fn', 'file.c', '8']], [['fn', null, 8]]]) {
                D.sources.callers[0] = chain;
                assert.throws(() => callSitePos(0), /Source line map read/);
            }
            assert.equal(legacyReads, 8);
        ''')


if __name__ == '__main__':
    unittest.main()
