import os
from dataclasses import dataclass
from pathlib import Path

# Auto-load .env when running locally.
# On Azure, env vars are injected by the Function App -- dotenv is a no-op.
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(dotenv_path=_env_path, override=False)
except ImportError:
    pass  # python-dotenv not installed in production -- that's fine


@dataclass(frozen=True)
class Config:
    """
    Central config loaded from environment variables.
    On Azure, these are injected via Key Vault references in Function App settings.
    Locally, loaded from a .env file via python-dotenv above.
    """

    # Azure identity
    tenant_id: str
    client_id: str | None       # Set locally (service principal), None on Azure (managed identity)
    client_secret: str | None   # Set locally (service principal), None on Azure (managed identity)

    # Microsoft Graph
    graph_base_url: str

    # Cosmos DB
    cosmos_endpoint: str
    cosmos_key: str
    cosmos_database: str

    # Azure OpenAI
    openai_endpoint: str
    openai_api_key: str
    openai_model: str

    # Service Bus
    servicebus_conn: str

    # Runtime
    environment: str
    bot_type: str

    # Local dev only — bypasses approval gate for testing
    # NEVER set this to true in production
    skip_approval: bool

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            tenant_id=_require("AZURE_TENANT_ID"),
            client_id=os.getenv("AZURE_CLIENT_ID"),
            client_secret=os.getenv("AZURE_CLIENT_SECRET"),
            graph_base_url=os.getenv("GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0"),
            cosmos_endpoint=os.getenv("COSMOS_DB_ENDPOINT", ""),
            cosmos_key=os.getenv("COSMOS_DB_KEY", ""),
            cosmos_database=os.getenv("COSMOS_DATABASE", "iam-db"),
            openai_endpoint=_require("OPENAI_ENDPOINT"),
            openai_api_key=_require("OPENAI_API_KEY"),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            servicebus_conn=os.getenv("SERVICE_BUS_CONN", ""),
            environment=os.getenv("ENVIRONMENT", "dev"),
            bot_type=os.getenv("BOT_TYPE", "unknown"),
            skip_approval=os.getenv("SKIP_APPROVAL", "false").lower() == "true",
        )


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            f"Add it to your .env file for local dev."
        )
    return value


def _validate_config(cfg: "Config") -> None:
    """
    Startup validation of critical configuration values. (Finding 11)
    Catches misconfigurations early before any API calls are made.
    """
    import re
    guid_re = re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        re.IGNORECASE,
    )

    # Validate tenant_id is a proper GUID
    if not cfg.tenant_id or not guid_re.match(cfg.tenant_id.strip()):
        raise EnvironmentError(
            "AZURE_TENANT_ID is not a valid GUID. "
            "Expected format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
        )

    # Validate OpenAI endpoint is a real HTTPS URL
    if not cfg.openai_endpoint.startswith("https://"):
        raise EnvironmentError(
            "OPENAI_ENDPOINT must start with 'https://'. "
            f"Got: {cfg.openai_endpoint[:30]}..."
        )

    # Validate Graph base URL points to Microsoft
    if not cfg.graph_base_url.startswith("https://graph.microsoft.com"):
        raise EnvironmentError(
            "GRAPH_BASE_URL must start with 'https://graph.microsoft.com'. "
            f"Got: {cfg.graph_base_url[:30]}..."
        )

    # Warn if SKIP_APPROVAL is enabled outside local dev
    if cfg.skip_approval and cfg.environment.lower() not in ("dev", "local", "docker"):
        import logging
        logging.getLogger(__name__).warning(
            "SKIP_APPROVAL=true is set in a non-dev environment (%s). "
            "This bypasses the approval gate — never use in production.",
            cfg.environment,
        )


# Singleton -- imported by all modules
config = Config.from_env()
_validate_config(config)

