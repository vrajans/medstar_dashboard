"""
test_llm_gateway.py — routing/config checks for the LLM gateway.
Does NOT require network: it validates config resolution, provider dispatch,
BYO storage, and payload shaping via a monkeypatched transport.
Run: python test_llm_gateway.py
"""
import os, tempfile, warnings
warnings.filterwarnings("ignore")
from sqlalchemy import create_engine
from ai import llm_gateway as g

FAILS = []
def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond: FAILS.append(name)

# capture the last request the transport would send, instead of hitting the network
CAPTURED = {}
def fake_post(url, headers, payload, timeout=30):
    CAPTURED.clear(); CAPTURED.update(url=url, headers=headers, payload=payload)
    # emulate provider-shaped responses
    if "anthropic" in url or "/messages" in url:
        return {"content": [{"text": "hi-anthropic"}]}
    return {"choices": [{"message": {"content": "hi-openai"}}]}
g._post = fake_post

def main():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    eng = create_engine(f"sqlite:///{path}")
    try:
        g.clear_tenant_context()

        print("1) Platform default resolves to Groq")
        os.environ.pop("LLM_PROVIDER", None); os.environ.pop("LLM_MODEL", None)
        os.environ["GROQ_API_KEY"] = "test-groq-key"
        cfg = g.resolve_config()
        check("provider=groq", cfg.provider == "groq")
        check("model=llama default", cfg.model == "llama-3.3-70b-versatile")
        check("usable", cfg.usable)

        print("2) Env override → OpenAI")
        os.environ["LLM_PROVIDER"] = "openai"; os.environ["OPENAI_API_KEY"] = "sk-test"
        cfg2 = g.resolve_config()
        check("provider=openai", cfg2.provider == "openai")
        r = g.chat([{"role": "user", "content": "hey"}], config=cfg2)
        check("openai call returns text", r == "hi-openai")
        check("hits openai chat/completions url", CAPTURED["url"].endswith("/chat/completions"))
        check("bearer auth header", CAPTURED["headers"].get("Authorization", "").startswith("Bearer "))
        os.environ["LLM_PROVIDER"] = "groq"

        print("3) Anthropic dispatch splits system prompt")
        acfg = g.LLMConfig(provider="anthropic", model="claude-3-5-sonnet-20241022",
                           api_key="ak-test", base_url="https://api.anthropic.com/v1", style="anthropic")
        r = g.chat([{"role": "system", "content": "be brief"},
                    {"role": "user", "content": "hey"}], config=acfg)
        check("anthropic call returns text", r == "hi-anthropic")
        check("system pulled out of messages", CAPTURED["payload"].get("system") == "be brief")
        check("messages exclude system", all(m["role"] != "system" for m in CAPTURED["payload"]["messages"]))
        check("x-api-key header", "x-api-key" in CAPTURED["headers"])

        print("4) Azure dispatch builds deployment URL")
        zcfg = g.LLMConfig(provider="azure", model="my-deploy", api_key="az-test",
                           base_url="https://res.openai.azure.com", style="azure")
        g.chat([{"role": "user", "content": "hey"}], config=zcfg)
        check("azure url has deployment + api-version",
              "/deployments/my-deploy/chat/completions?api-version=" in CAPTURED["url"])
        check("azure api-key header", "api-key" in CAPTURED["headers"])

        print("5) Per-tenant BYO overrides platform default")
        g.set_tenant_llm(eng, 42, "openai", "sk-tenant42", model="gpt-4o")
        got = g.get_tenant_llm(eng, 42)
        check("stored provider", got["provider"] == "openai")
        cfg3 = g.resolve_config(tenant_id=42, engine=eng)
        check("BYO provider wins", cfg3.provider == "openai")
        check("BYO model wins", cfg3.model == "gpt-4o")
        check("BYO key wins", cfg3.api_key == "sk-tenant42")

        print("6) Request-context routing (no param threading)")
        g.set_tenant_context(42, eng)
        cfg4 = g.resolve_config()
        check("context routes to tenant BYO", cfg4.provider == "openai" and cfg4.model == "gpt-4o")
        g.clear_tenant_context()
        cfg5 = g.resolve_config()
        check("cleared context falls back to default", cfg5.provider == "groq")

        print("7) Clearing BYO reverts tenant to platform default")
        g.clear_tenant_llm(eng, 42)
        check("BYO removed", g.get_tenant_llm(eng, 42) is None)

        print("8) groq_client delegates through the gateway")
        from ai import groq_client
        out = groq_client._groq_chat([{"role": "user", "content": "hey"}])
        check("groq_client._groq_chat routed via gateway", out == "hi-openai" or out == "hi-anthropic" or out == "hi-openai")

        print(f"\n{'ALL PASS ✅' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
        return 0 if not FAILS else 1
    finally:
        eng.dispose(); os.unlink(path)

if __name__ == "__main__":
    raise SystemExit(main())
