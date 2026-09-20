"""The artifact side: scene documents in, self-contained HTML pages out.

Nothing here computes geometry. Every position, polyline and judgement is read from the
shim's JSON, and the one arithmetic this package does — putting a traversal into the
pattern's own frame — is checked against the shim in `tests/test_pattern_frame.py`.
"""
