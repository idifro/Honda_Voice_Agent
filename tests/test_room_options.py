"""Caller audio filter setting (NOISE_CANCELLATION) -> LiveKit room input options."""

from livekit.plugins import noise_cancellation

from honda_voice.agent.worker import room_options


def nc_of(mode):
    return room_options(mode).audio_input.noise_cancellation


def test_bvc_removes_background_voices():
    assert type(nc_of("bvc")) is type(noise_cancellation.BVC())


def test_nc_and_off():
    assert type(nc_of("nc")) is type(noise_cancellation.NC())
    assert nc_of("off") is None
