#! /usr/bin/env python

import numpy as np
from scipy.interpolate import interp1d
from io import BytesIO


# filename = "1.wav"
# samplerate = 22168  # frequency for F-3


def get_period(block, window_size):
    orig = np.asarray(block[0:window_size])
    best_diff = 999999
    best_offset = None
    for offset in range(10, len(block) - window_size, 1):
        shifted_wave = np.asarray(block[offset:window_size+offset])
        diffs = abs(orig - shifted_wave)
        diff = np.add.reduce(diffs)
        if diff < best_diff:
            best_offset = offset
            best_diff = diff

    return best_offset

def get_single_wave(block, period):
    slice = np.asarray(block[0:period])
    if slice.max() < 0 or slice.min() > 0:
        return slice
    i = slice.argmax()
    while slice[i] > 0:
        i = (i - 1) % period
    return np.concatenate((slice[i+1:], slice[0:i+1]))


class Frame:
    def __init__(self, frequency, amplitude, wave):
        self.frequency = frequency
        self.amplitude = amplitude
        self.wave = wave

    def packed_data(self):
        data_buffer = BytesIO()
        data_buffer.write(bytes([self.frequency & 255, (self.amplitude << 4) | (self.frequency >> 8)]))
        for i in range(0, 32, 2):
            data_buffer.write(bytes([
                self.wave[i] | (self.wave[i + 1] << 4)
            ]))
        return data_buffer.getvalue()

    def __repr__(self):
        return "<Frame: %f Hz, ampl %d, %r>" % (self.frequency, self.amplitude, self.wave)


def iter_blocks(mono_wave, block_step, block_size):
    i = 0
    while True:
        yield mono_wave[int(i):int(i)+block_size]
        i += block_step
        if i > len(mono_wave) - block_step:
            break


def make_frame(block, samplerate):
    period = get_period(block, int(len(block) / 2))
    freq = samplerate / period
    single_wave = get_single_wave(block, period)
    amplitude = abs(single_wave).max()
    norm_single_wave = single_wave / amplitude
    x_axis = np.linspace(0, 1, period)
    fn = interp1d(x_axis, norm_single_wave, 'cubic')

    final_ampl = min(int(amplitude*16), 15)
    if final_ampl == 0:
        final_wave = tuple([0]*32)
    else:
        final_wave = tuple(
            round(max(-0.999999, min(0.999999, fn(i))) * 7 + 8)
            for i in np.linspace(0, 1, 32)
        )

    return Frame(round(freq), final_ampl, final_wave)


def make_wavetable(mono_wave, samplerate):
    seen_waves = set()
    frames = []
    block_step = samplerate // 60  # hop size

    for block in iter_blocks(mono_wave, block_step, int(block_step*2)):
        frame = make_frame(block, samplerate)
        #if frame.wave in seen_waves:
        #    print("seen wave: %r" % (frame.wave, ))
        seen_waves.add(frame.wave)

        # print("%d (%f Hz, ampl %d): %r" % (block_num, freq, final_ampl, final_wave))
        # print("{%d, %d, {%s}}," % (round(freq), final_ampl, ", ".join([str(v) for v in final_wave])))
        frames.append(frame)

    return frames
