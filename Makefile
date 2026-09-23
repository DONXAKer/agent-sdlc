# Единая точка проверок репозитория методологии. Логика — в check.py (кроссплатформенно,
# на Windows без make: `python check.py`). Здесь только удобные алиасы.
PY ?= python3

.PHONY: check check-all sync templates-check corpus corpus-update consistency seed

check:            ## все проверки (без сверки глобальной установки)
	$(PY) check.py

check-all:        ## все проверки + ./install.sh --check
	$(PY) check.py --with-install

sync:             ## разлить корневые шаблоны и константы в копии скиллов
	./sync-templates.sh

templates-check:
	./sync-templates.sh --check

consistency:
	$(PY) tools/check-consistency.py

corpus:
	$(PY) test/run-corpus.py

corpus-update:    ## только после объяснения расхождения в теле коммита
	$(PY) test/run-corpus.py --update

seed:             ## самопосев flow-verdict.py: какие проверки корпус не ловит
	$(PY) test/seed-flow-verdict.py --report
