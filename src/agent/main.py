"""Amazon Bedrock AgentCore Runtime entrypoint (direct code deployment).

The runtime hosts the orchestration: it calls the frame-sampling tool, the
Nova model, the validator, the post-processors and the result store in a fixed
order defined in code. The model never chooses which tool runs during video
analysis, because the video itself is untrusted input. For questions, the
model may call exactly one read-only tool (search_events) scoped by code to
the caller's camera ids.
"""

from __future__ import annotations

import logging

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from video_events.service import safe_handle

logging.basicConfig(level=logging.INFO, format="%(message)s")
app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload, context=None):
    return safe_handle(payload if isinstance(payload, dict) else {})


if __name__ == "__main__":
    app.run()
