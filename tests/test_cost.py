from types import SimpleNamespace as NS

from stages.llm import token_cost, usage_and_cost


def test_token_cost_opus(cfg):
    # 1M in, 1M out on Opus 5.5 = $4 + $20
    assert token_cost(cfg, "claude-opus-5-5", input_tokens=1_000_000, output_tokens=1_000_000) == 24.0
    # cache reads at 0.05x
    assert abs(token_cost(cfg, "claude-opus-5-5", cache_read=1_000_000) - 0.20) < 1e-9


def test_unknown_model_priced_high(cfg):
    assert token_cost(cfg, "claude-mystery", output_tokens=1_000_000) == 40.0


def test_usage_with_search_and_fallback_iterations(cfg):
    msg = NS(model="claude-opus-4-8", usage=NS(
        input_tokens=3000, output_tokens=300, cache_read_input_tokens=0, cache_creation_input_tokens=0,
        cache_creation=None, server_tool_use=NS(web_search_requests=5, web_fetch_requests=2),
        iterations=[
            NS(type="message", model="claude-opus-5-5", input_tokens=1000, output_tokens=100,
               cache_read_input_tokens=0, cache_creation_input_tokens=0, cache_creation=None),
            NS(type="fallback_message", model="claude-opus-4-8", input_tokens=2000, output_tokens=200,
               cache_read_input_tokens=0, cache_creation_input_tokens=0, cache_creation=None),
        ]))
    u = usage_and_cost(cfg, "claude-opus-5-5", msg)
    expected = (1000 * 4 + 100 * 20 + 2000 * 5 + 200 * 25) / 1e6 + 5 * 10 / 1000
    assert abs(u.cost_usd - expected) < 1e-9
    assert u.served_by == ["claude-opus-5-5", "claude-opus-4-8"]
    assert u.web_searches == 5
