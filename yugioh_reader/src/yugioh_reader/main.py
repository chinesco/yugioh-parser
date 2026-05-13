"""Entrypoint for the Reachy Mini conversation app."""

import os
import sys
import time
import asyncio
import argparse
import threading
import logging
from typing import Any, Dict, List, Optional

import gradio as gr
from fastapi import FastAPI
from fastrtc import Stream
from gradio.utils import get_space

from reachy_mini import ReachyMini, ReachyMiniApp
from yugioh_reader.utils import (
    parse_args,
    setup_logger,
    handle_vision_stuff,
    log_connection_troubleshooting,
)


def update_chatbot(chatbot: List[Dict[str, Any]], response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Update the chatbot with AdditionalOutputs."""
    chatbot.append(response)
    return chatbot


def run(
    args: argparse.Namespace,
    robot: ReachyMini = None,
    app_stop_event: Optional[threading.Event] = None,
    settings_app: Optional[FastAPI] = None,
    instance_path: Optional[str] = None,
) -> None:
    """Run the Reachy Mini conversation app."""
    # Putting these dependencies here makes the dashboard faster to load when the conversation app is installed
    from yugioh_reader.moves import MovementManager
    from yugioh_reader.console import LocalStream
    from yugioh_reader.openai_realtime import OpenaiRealtimeHandler
    from yugioh_reader.gemini_realtime import GeminiRealtimeHandler
    from yugioh_reader.tools.core_tools import ToolDependencies
    from yugioh_reader.audio.head_wobbler import HeadWobbler

    logger = setup_logger(args.debug)
    logger.info("Starting Reachy Mini Conversation App")

    if args.no_camera and args.head_tracker is not None:
        logger.warning(
            "Head tracking disabled: --no-camera flag is set. "
            "Remove --no-camera to enable head tracking."
        )

    if robot is None:
        try:
            robot_kwargs = {}
            if args.robot_name is not None:
                robot_kwargs["robot_name"] = args.robot_name

            logger.info("Initializing ReachyMini (SDK will auto-detect appropriate backend)")
            robot = ReachyMini(**robot_kwargs)

        except TimeoutError as e:
            logger.error(
                "Connection timeout: Failed to connect to Reachy Mini daemon. "
                f"Details: {e}"
            )
            log_connection_troubleshooting(logger, args.robot_name)
            sys.exit(1)

        except ConnectionError as e:
            logger.error(
                "Connection failed: Unable to establish connection to Reachy Mini. "
                f"Details: {e}"
            )
            log_connection_troubleshooting(logger, args.robot_name)
            sys.exit(1)

        except Exception as e:
            logger.error(
                f"Unexpected error during robot initialization: {type(e).__name__}: {e}"
            )
            logger.error("Please check your configuration and try again.")
            sys.exit(1)

    # Auto-enable Gradio in simulation mode (both MuJoCo for daemon and mockup-sim for desktop app)
    status = robot.client.get_status()
    if isinstance(status, dict):
        simulation_enabled = status.get("simulation_enabled", False)
        mockup_sim_enabled = status.get("mockup_sim_enabled", False)
    else:
        simulation_enabled = getattr(status, "simulation_enabled", False)
        mockup_sim_enabled = getattr(status, "mockup_sim_enabled", False)

    if simulation_enabled or mockup_sim_enabled:
        logger.info("Simulation mode detected: Auto-enabling Gradio interface.")
        args.gradio = True

    camera_worker, head_tracker, vision_manager = handle_vision_stuff(args, robot)

    # Movement manager handles the 100Hz control loop
    movement_manager = MovementManager(robot, camera_worker)
    movement_manager.start()

    # Head wobbler handles audio-to-motion synchronization
    head_wobbler = HeadWobbler(movement_manager)

    deps = ToolDependencies(
        current_robot=robot,
        movement_manager=movement_manager,
        camera_worker=camera_worker,
        vision_manager=vision_manager,
        head_wobbler=head_wobbler,
        app_stop_event=app_stop_event,
    )


    with gr.Blocks() as chatbot:
        with gr.Row():
            chatbot_ui = gr.Chatbot(
                label="Conversation",
                type="messages",
                avatar_images=(
                    str(Path(__file__).parent / "images" / "user_avatar.png"),
                    str(Path(__file__).parent / "images" / "reachymini_avatar.png"),
                ),
                scale=1,
            )

    logger.debug(f"Chatbot avatar images: {chatbot_ui.avatar_images}")

    if args.gemini:
        handler = GeminiRealtimeHandler(deps, gradio_mode=args.gradio, instance_path=instance_path)
        logger.info("Using Gemini Multimodal Live backend")
    else:
        handler = OpenaiRealtimeHandler(deps, gradio_mode=args.gradio, instance_path=instance_path)
        logger.info("Using OpenAI Realtime backend")

    # Initialize the app and stream manager
    if not settings_app:
        app = FastAPI()
    else:
        app = settings_app

    stream_manager: gr.Blocks | LocalStream | None = None

    if args.gradio:
        # In Gradio mode, we still want the settings routes on the FastAPI app
        # so that the landing page can save the API key if missing.
        LocalStream(
            handler,
            robot,
            settings_app=app,
            instance_path=instance_path,
        )._init_settings_ui_if_needed()
        
        with gr.Blocks() as stream_manager:
            with gr.Row():
                with gr.Column():
                    api_key_textbox = gr.Textbox(
                        label="API Key",
                        type="password",
                        placeholder="Enter API Key here if not in .env",
                    )
                    profile_selector = gr.Dropdown(
                        label="Selected Personality Profile",
                        choices=["_yugioh_reader_locked_profile"],
                        value="_yugioh_reader_locked_profile",
                    )
                    apply_btn = gr.Button("Apply Personality")
                    status_md = gr.Markdown("")

                with gr.Column():
                    stream = Stream(handler, multimodal=True)
                    stream.render()

            apply_btn.click(
                fn=handler.apply_personality,
                inputs=[profile_selector],
                outputs=[status_md],
            )

        stream_manager.queue()
        gr.mount_gradio_app(app, stream_manager, path="/chat")

    else:
        # Headless console mode
        stream_manager = LocalStream(
            handler,
            robot,
            settings_app=app,
            instance_path=instance_path,
        )

    # Logic to stop the app when the stop event is set (either by robot or UI)
    def check_stop_event():
        if app_stop_event is not None:
            while not app_stop_event.is_set():
                time.sleep(0.1)
            logger.info("App stop event detected, shutting down...")
            if hasattr(stream_manager, "close"):
                stream_manager.close()
            movement_manager.stop()
            if camera_worker is not None:
                camera_worker.stop()
            sys.exit(0)

    threading.Thread(target=check_stop_event, daemon=True).start()

    if not args.gradio:
        stream_manager.run()
    else:
        import uvicorn
        logger.info("Gradio interface available at http://localhost:7860/chat")
        uvicorn.run(app, host="0.0.0.0", port=7860)


class YugiohReader(ReachyMiniApp):
    """Reachy Mini application for Yu-Gi-Oh card identification."""
    
    name = "yugioh_reader"
    description = "Yu-Gi-Oh! Card Expert"
    custom_app_url = "http://0.0.0.0:7860/chat"
    
    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        """Run the Reachy Mini conversation app."""
        args, _ = parse_args()
        instance_path = self._get_instance_path().parent
        
        run(
            args,
            robot=reachy_mini,
            app_stop_event=stop_event,
            instance_path=instance_path,
        )


if __name__ == "__main__":
    app = YugiohReader()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()
