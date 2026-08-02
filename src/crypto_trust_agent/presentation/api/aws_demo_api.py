"""Public AWS demo ASGI entrypoint."""

from crypto_trust_agent.presentation.api.aws_demo_composition import build_aws_demo_composition
from crypto_trust_agent.presentation.api.demo_ui_api import (
    DemoUiAsgiApp,
    DemoUiHttpHandler,
    create_demo_fastapi_app,
)
from crypto_trust_agent.presentation.api.demo_ui_composition import (
    DEMO_COOKIE_NAME,
    DEMO_USER_TOKEN,
)
from crypto_trust_agent.presentation.demo_ui.app import DemoApp


composition = build_aws_demo_composition()
handler = DemoUiHttpHandler(
    DemoApp(composition.use_case, live_mode=True),
    composition.use_case,
    composition.authenticator,
    browser_token=DEMO_USER_TOKEN,
    cookie_name=DEMO_COOKIE_NAME,
)
app = create_demo_fastapi_app(DemoUiAsgiApp(handler))
app.state.demo_composition = composition


__all__ = ("app", "composition")
