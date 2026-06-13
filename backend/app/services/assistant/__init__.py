"""Conversational AP Assistant.

A per-user, per-tenant natural-language assistant over a fixed, typed toolset
(five read-only tools, current tenant only). Local-first: the default `mock`
adapter routes queries deterministically with no network and no key; the
`claude` adapter (Anthropic Messages API tool-use) is selected only when a key
is configured. See ``backend/docs/conversational-assistant.md``.
"""
