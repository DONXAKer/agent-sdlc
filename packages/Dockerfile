FROM alpine:3.22

LABEL org.opencontainers.image.title="Комплекты Agent-SDLC"
LABEL org.opencontainers.image.description="Версионированные ZIP комплекты Agent-SDLC с SHA256 суммами"

COPY agent-sdlc-*.zip /packages/
COPY SHA256SUMS /packages/

CMD ["sleep", "infinity"]
