# PyTest Reference

## Setup & Detection
- Before writing tests, check `pyproject.toml` / `setup.cfg` / `pytest.ini` for existing config: markers, test paths, coverage settings, plugins already installed.
- Detect the test runner command from CI config or `pyproject.toml` (`[tool.pytest.ini_options]`) rather than assuming `pytest` with no args.
- Match existing file/function naming conventions (`test_*.py` vs `*_test.py`, `Test*` classes vs bare functions).

## Fixtures
- Use fixtures (`@pytest.fixture`) for reusable setup/teardown instead of duplicating setup code in every test.
- Prefer function-scoped fixtures by default; only widen scope (`module`, `session`) for expensive, read-only resources (e.g., a DB connection pool), and be explicit about why.
- Use `conftest.py` for fixtures shared across multiple test files — don't redefine the same fixture in every file.
- Use `yield` fixtures for setup/teardown pairs (e.g., open a resource, yield it, close it) instead of manual try/finally in every test.

## Parametrization
- Use `@pytest.mark.parametrize` for boundary/edge-case matrices instead of copy-pasted near-identical test functions.
- Give parametrize cases readable `ids` so failures are legible:
```python
  @pytest.mark.parametrize("email,valid", [
      ("a@b.com", True),
      ("", False),
      ("no-at-sign", False),
      (None, False),
  ], ids=["valid", "empty", "no-at", "none"])
  def test_email_validation(email, valid):
      ...
```

## Mocking
- Use `unittest.mock` (`Mock`, `MagicMock`, `patch`) or `pytest-mock`'s `mocker` fixture — pick whichever the project already uses.
- Patch at the point of use, not the point of definition (`patch("myapp.service.requests.get")`, not `patch("requests.get")`).
- Mock at system boundaries: HTTP calls, DB writes, filesystem, third-party SDKs, `datetime.now()`/`time.sleep`. Don't mock the code under test itself.
- For integration tests intentionally hitting a real (test) DB, say so explicitly and don't also mock the DB layer — pick one mode per test.

## Async
- Use `pytest-asyncio` (`@pytest.mark.asyncio`) for async functions; check if `asyncio_mode = "auto"` is already set in config before adding the decorator everywhere.
- Test promise/coroutine rejection paths explicitly with `pytest.raises`.

## Assertions & Errors
- Use plain `assert` (pytest rewrites these for good diff output) rather than `self.assertEqual`-style unless the project already uses `unittest.TestCase`.
- Use `pytest.raises(SomeError, match="...")` for exception cases — always assert on the exception type, and match message content when it's meaningful.

## Coverage
- Run with `pytest --cov=<package> --cov-report=term-missing` when a coverage target matters.
- Don't chase 100% — flag untested branches, prioritize business logic and error paths over trivial getters/pass-throughs.

## Common Pitfalls to Avoid
- Don't use real `time.sleep()` in tests — use `freezegun` or mock time.
- Don't leave shared mutable state between tests — reset via fixtures, not manual cleanup at the end of each test.
- Don't hardcode real credentials/tokens/PII in fixtures — use obviously-fake values (`"test@example.com"`, `"fake-token-123"`).