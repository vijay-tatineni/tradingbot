"""§15.5: Assert the regime tab scaffolding is rendered into dashboard.html.

bot/dashboard.py:_write_html() is the source of truth for web/dashboard.html;
this test reads what the template writes and checks for the regime tab
markers. If a future PR refactors the template (e.g., to a Jinja file)
this test still passes provided the output keeps the same markers.
"""
import os
from pathlib import Path

import pytest

from bot.dashboard import Dashboard


class _StubConfig:
    """Minimal config for Dashboard instantiation. Avoids real instruments.json
    side effects on the canonical web/dashboard.html."""
    def __init__(self, web_dir: str):
        self.web_dir = web_dir
        self.check_interval_mins = 5
        self.path = "stub.json"
        self.portfolio_loss_limit = 1000
        self.broker_type = "stub"
        self.active_instruments = []
        self.accum_instruments = []
        self._raw = {"layer1_active": [], "layer2_accumulation": []}


@pytest.fixture
def rendered_html(tmp_path):
    cfg = _StubConfig(str(tmp_path))
    Dashboard(cfg)
    out = Path(tmp_path) / "dashboard.html"
    assert out.exists(), "Dashboard.__init__ did not write dashboard.html"
    return out.read_text()


class TestRegimeScaffolding:
    """The six panes and the nav exist as static markup."""

    def test_nav_present(self, rendered_html):
        assert 'id="regimeNav"' in rendered_html
        assert '// REGIME LAYER' in rendered_html

    @pytest.mark.parametrize("pane_id", [
        "regime-pane-regime",
        "regime-pane-overlays",
        "regime-pane-routing",
        "regime-pane-shadow",
        "regime-pane-degradation",
        "regime-pane-pauses",
        "regime-pane-classify",
    ])
    def test_pane_present(self, rendered_html, pane_id):
        assert f'id="{pane_id}"' in rendered_html

    @pytest.mark.parametrize("data_tab", [
        "regime", "overlays", "routing", "shadow", "degradation",
        "pauses", "classify",
    ])
    def test_tab_button_present(self, rendered_html, data_tab):
        assert f'data-tab="{data_tab}"' in rendered_html


class TestClassifyTabScaffolding:
    """Classify tab scaffold elements: dropdown, buttons, result/error
    containers, modal markup, and the copy strings the user signed off on."""

    @pytest.mark.parametrize("element_id", [
        "classifyBudget",
        "classifyInstrument",
        "classifyCostEstimate",
        "classifyTooltip",
        "classifyBtn",
        "classifyShowLastBtn",
        "classifyProgress",
        "classifyError",
        "classifyResult",
        "classifyModal",
        "classifyModalBackdrop",
        "classifyModalTitle",
        "classifyModalBody",
        "classifyModalCancel",
        "classifyModalConfirm",
    ])
    def test_element_present(self, rendered_html, element_id):
        assert f'id="{element_id}"' in rendered_html

    def test_subtitle_copy(self, rendered_html):
        assert "Re-classify an instrument using its most recent cached features." in rendered_html

    def test_section_heading_copy(self, rendered_html):
        assert ">Re-classify<" in rendered_html

    def test_tooltip_copy(self, rendered_html):
        # Key phrases from the agreed copy
        assert "Re-runs Claude on the most recent cached features" in rendered_html
        assert "low-confidence response" in rendered_html
        assert "last daily scheduler run" in rendered_html

    def test_classify_all_option_offered(self, rendered_html):
        """JS will inject ALL INSTRUMENTS into the dropdown at runtime;
        the static placeholder still hints at loading state."""
        assert "Loading instruments..." in rendered_html


class TestEndpointUrls:
    """The fetch URLs appear in the rendered JS."""

    @pytest.mark.parametrize("url", [
        "/api/regime/states",
        "/api/overlays/active",
        "/api/routing/decisions",
        "/api/shadow/comparison",
        "/api/degradation/events",
        "/api/pauses/list",
        "/api/regime/budget",
        "/api/regime/instruments",
        "/api/regime/classify",
    ])
    def test_url_present(self, rendered_html, url):
        assert f"'{url}" in rendered_html or f'"{url}' in rendered_html


class TestClassifyTabJS:
    """The Classify tab JS scaffolding: tab-activation hook, fetch helpers,
    modal opener, result card renderer, and the user-facing copy strings."""

    @pytest.mark.parametrize("fn_name", [
        "initializeClassifyTab",
        "_classifyRefreshBudget",
        "_classifyPopulateDropdown",
        "_classifyUpdateCostEstimate",
        "_classifyOpenModal",
        "_classifyRenderResultCard",
        "_classifyRun",
        "_classifyOnClick",
        "_classifyShowLast",
    ])
    def test_function_present(self, rendered_html, fn_name):
        assert f"function {fn_name}(" in rendered_html or \
               f"async function {fn_name}(" in rendered_html

    def test_activation_hooks_classify(self, rendered_html):
        """activateRegimeTab must short-circuit to initializeClassifyTab
        rather than calling fetchRegimeTab(key)."""
        assert "key === 'classify'" in rendered_html
        assert "initializeClassifyTab()" in rendered_html

    def test_single_classify_modal_copy(self, rendered_html):
        assert "This will replace today's cached classification." in rendered_html

    def test_all_classify_modal_copy(self, rendered_html):
        # Agreed copy contains all three phrases:
        assert "using their most recently cached features" in rendered_html
        assert "This will replace today's cached classifications" in rendered_html
        assert "Takes about 30 seconds" in rendered_html

    def test_result_card_features_dated_line(self, rendered_html):
        assert "Features dated:" in rendered_html

    def test_show_last_uses_states_endpoint(self, rendered_html):
        # _classifyShowLast reads from /api/regime/states (no fresh API call)
        assert "Last cached classification — no fresh API call made." in rendered_html


class TestFStringEscaping:
    """Catch f-string escape regressions: no stray {{ or }} in output, JS
    template literals render as ${...} not ${{...}}."""

    def test_no_unescaped_double_braces(self, rendered_html):
        # double-braces in output would mean we forgot to escape a brace,
        # OR escaped a literal that should've stayed literal. Either way it
        # signals a template bug.
        assert "{{" not in rendered_html
        assert "}}" not in rendered_html

    def test_template_literals_intact(self, rendered_html):
        # Regime JS uses ${...} template literals heavily; if escaping went
        # wrong they'd render as ${{...}}. Sample a few:
        assert "${v}" in rendered_html  # from regimePill / enginePill
        assert "${key}" in rendered_html  # from fetchRegimeTab querySelector
        assert "${r.status}" in rendered_html  # from fetchRegimeTab error path


class TestExistingDashboardIntact:
    """Don't regress the Layer 1 / Layer 2 sections."""

    def test_layer1_present(self, rendered_html):
        assert "LAYER 1" in rendered_html
        assert 'id="layer1"' in rendered_html

    def test_layer2_present(self, rendered_html):
        assert "LAYER 2" in rendered_html
        assert 'id="layer2"' in rendered_html

    def test_navbar_present(self, rendered_html):
        assert 'id="navUser"' in rendered_html
        assert "logout()" in rendered_html
