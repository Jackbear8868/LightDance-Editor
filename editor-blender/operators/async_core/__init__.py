import bpy
import sys

import asyncio
from concurrent.futures import ThreadPoolExecutor

from typing import Coroutine

from ...core.states import states

from ...core.models import (
    ControlMapElement,
    ControlMapStatus
)


# NOTE: Testing
def setup_test_states():
    for i, frame in enumerate(range(0, 240000, 2000)):
        control_map = states.control_map
        map_id = str(i)
        control_map[map_id] = ControlMapElement(
            start=frame,
            fade=False,
            status={}
        )


def setup_asyncio_executor():
    """Sets up AsyncIO to run properly on each platform."""

    if sys.platform == 'win32':
        asyncio.get_event_loop().close()
        # On Windows, the default event loop is SelectorEventLoop, which does
        # not support subprocesses. ProactorEventLoop should be used instead.
        # Source: https://docs.python.org/3/library/asyncio-subprocess.html
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
    else:
        loop = asyncio.get_event_loop()

    executor = ThreadPoolExecutor(max_workers=10)
    loop.set_default_executor(executor)


def tick_loop() -> bool:
    loop = asyncio.get_event_loop()
    if loop.is_closed():
        return True

    stop_after_this_kick = False
#    all_tasks = asyncio.all_tasks(loop)
#    if not len(all_tasks):
#        stop_after_this_kick = True
#    elif all(task.done() for task in all_tasks):
#        stop_after_this_kick = True

    loop.stop()
    loop.run_forever()

    return stop_after_this_kick


class AsyncLoopModalOperator(bpy.types.Operator):
    bl_idname = 'lightdance.async_loop'
    bl_label = 'Runs the asyncio main loop'

    def __del__(self):
        wm = bpy.context.window_manager
        setattr(wm, "ld_is_running", False)

    def execute(self, context: bpy.types.Context):
        return {'FINISHED'}

    def invoke(self, context: bpy.types.Context, _: bpy.types.Event):
        is_running = getattr(context.window_manager, "ld_is_running", False)

        # NOTE: Testing
        setup_test_states()

        print(f"invoke {is_running}")

        if is_running:
            return {'PASS_THROUGH'}

        context.window_manager.modal_handler_add(self)
        setattr(context.window_manager, "ld_is_running", True)

        wm = context.window_manager
        self.timer = wm.event_timer_add(0.001, window=context.window)

        return {'RUNNING_MODAL'}

    def modal(self, context: bpy.types.Context, event: bpy.types.Event):
        is_running = getattr(context.window_manager, "ld_is_running", False)

        if not is_running:
            return {'FINISHED'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        stop = tick_loop()
        if stop:
            wm = context.window_manager
            wm.event_timer_remove(self.timer)
            setattr(wm, "ld_is_running", False)

            return {'FINISHED'}

        return {'RUNNING_MODAL'}


class AsyncOperator(bpy.types.Operator):
    bl_idname = "lightdance.async_operator"
    bl_label = "Base class of async operator"

    def invoke(self, context: bpy.types.Context, _: bpy.types.Event):
        self.state = 'RUNNING'
        self.stop_upon_exception = False

        context.window_manager.modal_handler_add(self)
        self.timer = context.window_manager.event_timer_add(
            1 / 15, window=context.window)

        self._new_async_task(self.async_execute(context))

        return {'RUNNING_MODAL'}

    async def async_execute(self, context: bpy.types.Context):
        """Entry point of the asynchronous operator.

        Implement in a subclass.
        """
        await asyncio.sleep(1)
        print("async execute")
        return

    def quit(self):
        """Signals the state machine to stop this operator from running."""
        self.state = 'QUIT'

    def execute(self, context: bpy.types.Context):
        return {'FINISHED'}

    def modal(self, context, _: bpy.types.Event):
        task = self.async_task

        if self.state != 'EXCEPTION' and task and task.done() and not task.cancelled():
            ex = task.exception()
            if ex is not None:
                self.state = 'EXCEPTION'
                if self.stop_upon_exception:
                    self.quit()
                    self._finish(context)
                    return {'FINISHED'}

                return {'RUNNING_MODAL'}

        if self.state == 'QUIT':
            self._finish(context)
            return {'FINISHED'}

        return {'PASS_THROUGH'}

    def _finish(self, context: bpy.types.Context):
        self._stop_async_task()
        if self.timer is not None:
            context.window_manager.event_timer_remove(self.timer)

    def _new_async_task(self, async_task: Coroutine):
        """Stops the currently running async task, and starts another one."""
        self.async_task = asyncio.ensure_future(async_task)

    def _stop_async_task(self):
        if self.async_task is None:
            return

        # Signal that we want to stop.
        self.async_task.cancel()

        # Wait until the asynchronous task is done.
        if not self.async_task.done():
            loop = asyncio.get_event_loop()
            try:
                loop.run_until_complete(self.async_task)
            except asyncio.CancelledError:
                return

        # noinspection PyBroadException
        try:
            # This re-raises any exception of the task.
            self.async_task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass


def register():
    setup_asyncio_executor()
    bpy.utils.register_class(AsyncLoopModalOperator)
    bpy.utils.register_class(AsyncOperator)


def unregister():
    bpy.utils.unregister_class(AsyncLoopModalOperator)
    bpy.utils.unregister_class(AsyncOperator)