import asyncio
import base64
import json
import logging
import uuid
from typing import Any, Tuple, Optional, Literal
from datetime import datetime

import cv2
import numpy as np
from google import generativeai as genai
from fastrtc import AdditionalOutputs, AsyncStreamHandler, wait_for_item, audio_to_int16
from numpy.typing import NDArray
from scipy.signal import resample

from yugioh_reader.config import config
from yugioh_reader.prompts import get_session_voice, get_session_instructions
from yugioh_reader.tools.core_tools import ToolDependencies, get_tool_specs
from yugioh_reader.tools.background_tool_manager import BackgroundToolManager, ToolCallRoutine, ToolNotification

logger = logging.getLogger(__name__)

GEMINI_SAMPLE_RATE = 24000

class GeminiRealtimeHandler(AsyncStreamHandler):
    """A Gemini Multimodal Live handler for Reachy Mini."""

    def __init__(self, deps: ToolDependencies, gradio_mode: bool = False, instance_path: Optional[str] = None):
        super().__init__(
            expected_layout="mono",
            output_sample_rate=GEMINI_SAMPLE_RATE,
            input_sample_rate=GEMINI_SAMPLE_RATE,
        )
        self.deps = deps
        self.gradio_mode = gradio_mode
        self.instance_path = instance_path
        self.output_queue = asyncio.Queue()
        self.tool_manager = BackgroundToolManager()
        self.session = None
        self.last_activity_time = asyncio.get_event_loop().time()

    def copy(self) -> "GeminiRealtimeHandler":
        return GeminiRealtimeHandler(self.deps, self.gradio_mode, self.instance_path)

    async def start_up(self) -> None:
        """Initialize the Gemini Multimodal Live session."""
        import os
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:

            logger.error("GOOGLE_API_KEY missing from environment/.env")
            return

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash-exp")
        
        # Start the background tool manager
        self.tool_manager.start_up(tool_callbacks=[self._handle_tool_result])

        async with model.start_chat(history=[]) as session:
            self.session = session
            logger.info("Gemini Multimodal Live session started.")
            
            # Initial prompt/setup
            instructions = "MANDATORY: ALWAYS RESPOND IN ENGLISH. NEVER SPEAK VIETNAMESE. \n\n" + get_session_instructions()
            await self.session.send_message(instructions)
            
            # Background task to stream video frames to Gemini
            asyncio.create_task(self._stream_video())
            
            # Keep session alive and handle incoming events
            # (In a real Bidi-stream, we would iterate over the response stream here)
            # This is a high-level representation of the loop.
            while True:
                await asyncio.sleep(1)

    async def _stream_video(self):
        """Streams camera frames to Gemini at a low frequency."""
        while self.session:
            if self.deps.camera_worker is not None and not self.deps.movement_manager.is_muted:
                frame = self.deps.camera_worker.get_latest_frame()
                if frame is not None:
                    # Convert to RGB and encode for Gemini
                    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    _, encoded_image = cv2.imencode('.jpg', rgb_frame)
                    # Send frame to Gemini as a part of the multimodal stream
                    # await self.session.send_message([encoded_image.tobytes(), "Vision update"])
            await asyncio.sleep(1.0) # 1 FPS for vision awareness

    async def _handle_tool_result(self, bg_tool: ToolNotification) -> None:
        """Process the result of a Gemini tool call."""
        if not self.session: return
        
        result = bg_tool.result or {"error": bg_tool.error or "Unknown error"}
        logger.info(f"Tool {bg_tool.tool_name} finished. Sending result to Gemini.")
        
        # Send result back to Gemini
        await self.session.send_message(f"Tool result for {bg_tool.tool_name}: {json.dumps(result)}")

    async def receive(self, frame: Tuple[int, NDArray[np.int16]]) -> None:
        """Receive audio from mic and send to Gemini."""
        if not self.session or self.deps.movement_manager.is_muted:
            return
        
        # Logic to send audio chunks to Gemini's multimodal stream
        # This would use the GenAI SDK's streaming features
        pass

    async def emit(self) -> Tuple[int, NDArray[np.int16]] | AdditionalOutputs | None:
        """Emit audio from Gemini to Reachy's speakers."""
        return await wait_for_item(self.output_queue)

    async def shutdown(self) -> None:
        """Shutdown Gemini session."""
        await self.tool_manager.shutdown()
        self.session = None
