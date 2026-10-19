"""Offline SDK/HTTP image protocol, fallback classification and lifecycle tests."""
from __future__ import annotations

import json
import unittest
from dataclasses import replace
from unittest.mock import patch

import httpx as _httpx
from openai import _base_client, DefaultAsyncHttpxClient

from app.config import Settings
from app.llm import client as llm
from app.llm.providers import Provider

httpx = getattr(_base_client, "httpx2", _httpx)
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}}
IMAGE = {"mime": "image/png", "content_base64": "aW1hZ2U="}
SETTINGS = Settings(_env_file=None, llm_mock=False, llm_timeout_s=2,
                    llm_model="vision-test", llm_structured_json_schema=True)
PROVIDER = Provider(id="image-offline", label="Offline", api_key="offline-fake-key",
                    base_url="https://provider.invalid/v1", model="vision-test", source="env")


def completion(request, dialect):
    if dialect == "responses":
        return httpx.Response(200, request=request, json={
            "id": "offline", "object": "response", "created_at": 0, "status": "completed",
            "model": "vision-test", "output": [{"type": "message", "role": "assistant",
                "id": "out", "status": "completed", "content": [
                    {"type": "output_text", "text": '{"answer":"ok"}', "annotations": []}]}],
            "usage": {"input_tokens": 11, "output_tokens": 4, "total_tokens": 15}})
    return httpx.Response(200, request=request, json={
        "id": "offline", "object": "chat.completion", "created": 0, "model": "vision-test",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {
            "role": "assistant", "content": '{"answer":"ok"}'}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15}})


class ImageTransportTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await llm.shutdown_clients()
        llm.reset_run_metrics()
        self.requests = []
        self.clients = []
        self.errors = []
        self.provider = replace(PROVIDER, api="chat_completions")

        def handler(request):
            self.requests.append(request)
            if self.errors:
                status, message = self.errors.pop(0)
                return httpx.Response(status, request=request, headers={"x-should-retry": "false"},
                                      json={"error": {"message": message}})
            return completion(request, "responses" if request.url.path.endswith("responses") else "chat_completions")

        def factory(**kwargs):
            client = DefaultAsyncHttpxClient(transport=httpx.MockTransport(handler), **kwargs)
            self.clients.append(client)
            return client

        self.patches = [patch.object(llm, "get_settings", lambda: SETTINGS),
                        patch.object(llm, "current_provider", lambda: self.provider),
                        patch("app.llm.providers.guard_request_base_url", lambda *args: None),
                        patch.object(llm, "DefaultAsyncHttpxClient", factory)]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        await llm.shutdown_clients()
        self.assertTrue(all(c.is_closed for c in self.clients))
        for item in reversed(self.patches):
            item.stop()

    async def invoke(self, **kwargs):
        return await llm.chat_structured("read image", SCHEMA, system="system",
            history=[{"role": "user", "content": "prior"}], no_think=False,
            schema_in_prompt=False, **kwargs)

    async def test_chat_image_payload_schema_usage_and_pool(self):
        self.assertEqual(await self.invoke(images=[IMAGE]), {"answer": "ok"})
        request = self.requests[-1]
        body = json.loads(request.content)
        self.assertEqual(request.url.path, "/v1/chat/completions")
        self.assertEqual(request.headers["authorization"], "Bearer offline-fake-key")
        self.assertEqual(body["model"], "vision-test")
        self.assertEqual(body["messages"][-1]["content"], [
            {"type": "text", "text": "read image"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1hZ2U="}}])
        self.assertEqual(body["messages"][1], {"role": "user", "content": "prior"})
        self.assertEqual(body["response_format"]["json_schema"]["schema"], SCHEMA)
        self.assertEqual(llm.get_run_metrics()["prompt_tokens"], 11)
        await self.invoke(images=[IMAGE])
        self.assertEqual(len(self.clients), 1)

    async def test_responses_image_payload_schema_and_usage(self):
        self.provider = replace(self.provider, api="responses")
        self.assertEqual(await self.invoke(images=[IMAGE]), {"answer": "ok"})
        body = json.loads(self.requests[-1].content)
        self.assertEqual(body["instructions"], "system")
        self.assertEqual(body["input"][-1]["content"], [
            {"type": "input_text", "text": "read image"},
            {"type": "input_image", "image_url": "data:image/png;base64,aW1hZ2U="}])
        self.assertEqual(body["text"]["format"]["schema"], SCHEMA)
        self.assertEqual(llm.get_run_metrics()["total_tokens"], 15)

    async def test_text_payload_unchanged_both_protocols(self):
        for dialect in ("chat_completions", "responses"):
            self.provider = replace(self.provider, api=dialect)
            await self.invoke()
            body = json.loads(self.requests[-1].content)
            messages = body.get("messages", body.get("input"))
            self.assertEqual(messages[-1], {"role": "user", "content": "read image"})

    async def test_images_use_conversation_model_not_structured_model(self):
        self.provider = replace(self.provider, model="vision-model", structured_model="text-classifier")
        await self.invoke(images=[IMAGE])
        self.assertEqual(json.loads(self.requests[-1].content)["model"], "vision-model")
        await self.invoke()
        self.assertEqual(json.loads(self.requests[-1].content)["model"], "text-classifier")
        await self.invoke(images=[IMAGE], model="explicit-vision")
        self.assertEqual(json.loads(self.requests[-1].content)["model"], "explicit-vision")

    async def test_clear_unsupported_is_distinct_and_redacted(self):
        for message in ("This model does not support image inputs offline-fake-key",
                        "Image input is not supported", "Unsupported content type: 'image_url'",
                        "image_url is only supported by certain models", "This model only supports text input"):
            self.errors = [(400, message)]
            before = len(self.requests)
            with self.assertRaises(llm.ImageInputUnsupported) as ctx:
                await self.invoke(images=[IMAGE])
            self.assertNotIn("offline-fake-key", str(ctx.exception))
            self.assertEqual(len(self.requests) - before, 1)

    async def test_operational_and_invalid_image_errors_never_trigger_ocr(self):
        for status, message in ((401, "model does not support images"),
                                (429, "image input not supported"),
                                (503, "image input not supported"),
                                (400, "Invalid image format"),
                                (400, "unsupported image format: bmp"),
                                (400, "model does not support image format: bmp"),
                                (400, "image too large")):
            self.errors = [(status, message)]
            with self.assertRaises(llm.LLMUnavailable) as ctx:
                await self.invoke(images=[IMAGE])
            self.assertNotIsInstance(ctx.exception, llm.ImageInputUnsupported)

    async def test_parameter_retry_preserves_image(self):
        self.errors = [(400, "Unsupported parameter: response_format")]
        await self.invoke(images=[IMAGE])
        self.assertEqual(len(self.requests), 2)
        bodies = [json.loads(r.content) for r in self.requests]
        self.assertNotIn("response_format", bodies[1])
        self.assertEqual(bodies[0]["messages"], bodies[1]["messages"])

    async def test_auto_dialect_retry_converts_image(self):
        self.provider = replace(self.provider, api="auto")
        llm._DIALECT_CACHE.clear()
        self.errors = [(404, "Unknown request URL: /chat/completions")]
        await self.invoke(images=[IMAGE])
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(json.loads(self.requests[-1].content)["input"][-1]["content"][1]["type"], "input_image")

    async def test_plain_text_errors_are_not_image_fallback(self):
        self.errors = [(400, "Model does not support image input")]
        with self.assertRaises(llm.LLMUnavailable) as ctx:
            await self.invoke()
        self.assertNotIsInstance(ctx.exception, llm.ImageInputUnsupported)


if __name__ == "__main__":
    unittest.main()
