FROM alpine:3.22

LABEL org.opencontainers.image.title="Комплекты Agent-SDLC"
LABEL org.opencontainers.image.description="Версионированные ZIP комплекты Codex, Claude Code и Kimi Code с SHA256 суммами"

COPY agent-sdlc-codex.zip /packages/
COPY agent-sdlc-claude-code.zip /packages/
COPY agent-sdlc-kimi-code.zip /packages/
COPY SHA256SUMS /packages/

CMD ["sleep", "infinity"]
