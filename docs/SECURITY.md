# Security

Public data GET only; no signing, wallets, trading endpoints or authentication in v0.1.0. `.env`, runtime and data are gitignored. Startup rejects `TRADING_ENABLED=true` or `LIVE_EXECUTION_APPROVED=true`. Future live trading requires independent exchange/jurisdiction eligibility confirmation, manual approvals and operational security review. No VPN/KYC workarounds.
