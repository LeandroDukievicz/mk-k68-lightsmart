"""Keeps the speed scale off whatever this machine measured.

``matrix.frame_ms`` reads a calibration file when the user has taken a
reading, which is the point of it -- and it means a checkout on a
calibrated keyboard would otherwise get different numbers from one that
never measured. Anything asserting milliseconds mixes this in and says
which scale it means.
"""

from __future__ import annotations

from mk_k68_lightsmart import matrix


class PinnedRate:
    """Pin the ms-per-unit for the length of one test."""

    #: The scale under test. Subclasses override to measure elsewhere.
    UNIT = matrix.FALLBACK_MS_PER_SPEED_UNIT

    def setUp(self) -> None:
        super().setUp()
        matrix.set_ms_per_speed_unit(self.UNIT)
        # Back to reading the file, whatever it says, for anything else.
        self.addCleanup(matrix.reload_calibration)

    def assert_closest_setting(self, setting: int, target_ms: float) -> None:
        """``setting`` is the best the scale can do for ``target_ms``.

        A coarse scale cannot land on a round number -- at 12 ms a unit
        the nearest thing to 100 ms is 97 -- so what is checked is that
        nothing else on the scale is nearer, not that the error is small.
        """
        best = min(range(1, 101),
                   key=lambda s: abs(matrix.frame_ms(s) - target_ms))
        self.assertAlmostEqual(matrix.frame_ms(setting),
                               matrix.frame_ms(best), places=6,
                               msg=f"setting {setting} is not the closest to "
                                   f"{target_ms} ms; {best} is")
