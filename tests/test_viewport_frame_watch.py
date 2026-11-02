"""The viewport frame watch (engine/viewport.py, _watch_frames()).

A timer on the main thread asks for a draw only when a film read is done,
and starts a read only when the film generation moved since the last read
(RTPATHCPU returns the generation passed until the first frame after an edit
is complete). It holds the engine by a weak reference and ends with it.
Stand-ins for the engine, the session and the framebuffer, plus one real
session for the pyluxcore check.

blender -b --factory-startup --python tests/test_viewport_frame_watch.py -- <site-packages>
"""

import gc
import os
import sys
import weakref

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.setup()

import pyluxcore
from blendluxcore.engine import viewport

checks = harness.Checks("test_viewport_frame_watch")


class Session:
    def __init__(self, generation):
        self.generation = generation
        self.paused = False
        self.waits = []

    def IsInPause(self):
        return self.paused

    def WaitNewFrame(self, generation=None, timeout=None):
        self.waits.append(generation)
        return self.generation


class FrameBuffer:
    def __init__(self):
        self.read_done = False
        self.reading = False
        self.reads = 0

    def finish_read(self):
        done, self.read_done = self.read_done, False
        if done:
            self.reading = False
        return done

    def is_denoiser_active(self):
        return False

    def wait_read(self):
        self.reading = False

    def start_read(self, session):
        if self.reading:
            return False
        self.reading = True
        self.reads += 1
        return True


class Engine:
    def __init__(self, session):
        self.session = session
        self.framebuffer = FrameBuffer()
        self.viewport_watch_frames = True
        self.starting_session = False
        self.viewport_film_key = None
        self.redraws = 0

    def tag_redraw(self):
        self.redraws += 1


session = Session(0)
engine = Engine(session)
ref = weakref.ref(engine)
tick = lambda: viewport._watch_frames(ref)

checks.check(tick() == 0.0 and engine.framebuffer.reads == 0 and engine.redraws == 0,
             "no complete frame yet (generation 0): no read, no draw")

session.generation = 3
tick()
checks.check(engine.framebuffer.reads == 1 and engine.viewport_film_key == (id(session), 3),
             f"a new frame starts a read ({engine.viewport_film_key})")
checks.check(engine.redraws == 0, "no draw while the read runs")

session.generation = 4
tick()
checks.check(engine.framebuffer.reads == 1, "no second read while one runs")

engine.framebuffer.read_done = True
tick()
checks.check(engine.redraws == 1, "a finished read asks for a draw")
checks.check(engine.framebuffer.reads == 2 and engine.viewport_film_key == (id(session), 4),
             "the frame that came during the read is read next")
checks.check(session.waits[-1] == 3, f"the wait gets the generation of the last read ({session.waits[-1]})")

engine.framebuffer.read_done = True
tick()
tick()
checks.check(engine.framebuffer.reads == 2 and engine.redraws == 2,
             "the same generation: no read, one draw for the finished read")

session.paused = True
waits = len(session.waits)
session.generation = 5
tick()
checks.check(len(session.waits) == waits and engine.framebuffer.reads == 2, "paused: no wait, no read")
session.paused = False

other = Session(0)
engine.session = other
tick()
checks.check(other.waits == [0] and engine.framebuffer.reads == 2,
             f"a new session: the wait gets 0 ({other.waits}), and nothing to read at 0")

engine.viewport_watch_frames = False
waits = len(other.waits)
checks.check(tick() == 0.0 and len(other.waits) == waits, "off (material preview, older pyluxcore): idle")

del engine
gc.collect()
checks.check(tick() is None, "the timer ends with the engine")


class FreedEngine:
    @property
    def session(self):
        raise ReferenceError("StructRNA of type LuxCoreRenderEngine has been removed")


freed = FreedEngine()
checks.check(viewport._watch_frames(weakref.ref(freed)) is None,
             "the timer ends when Blender freed the engine's struct")

# The loaded pyluxcore: the timed wait, returning at once on a session that
# is not started
props = pyluxcore.Properties()
props.SetFromString("""
    renderengine.type = RTPATHCPU
    sampler.type = RTPATHCPUSAMPLER
    film.width = 16
    film.height = 16
    """)
scene = pyluxcore.Scene()
scene.Parse(pyluxcore.Properties().SetFromString("scene.camera.type = perspective"))
real = pyluxcore.RenderSession(pyluxcore.RenderConfig(props, scene))
# The overloads pybind11 lists
expected = "timeoutMs" in (pyluxcore.RenderSession.WaitNewFrame.__doc__ or "")
checks.check(viewport._timed_wait(real) == expected,
             f"the timed WaitNewFrame() found as pyluxcore has it ({expected})")

checks.finish()
