from modfile import ModFile
from wavetable import make_wavetable
from io import BytesIO
from ticfile import TICFile, Chunk, ChunkType
from collections import defaultdict

mod = ModFile.open("GUITAROU.MOD")

wavetable_data_buffer = BytesIO()
wavetable_data_length = 0
sample_meta = []

# determine the average pitch used for each sample
pitch_sums_by_sample = defaultdict(lambda:0)
note_counts_by_sample = defaultdict(lambda:0)
for pattern in mod.patterns:
    for row in pattern:
        for (note, sample, effect, param) in row:
            if note is not None:
                pitch_sums_by_sample[sample-1] += note
                note_counts_by_sample[sample-1] += 1

avg_notes_by_sample = {}
for i in range(0, len(mod.samples)):
    if note_counts_by_sample[i]:
        avg_notes_by_sample[i] = int(pitch_sums_by_sample[i] / note_counts_by_sample[i])
    else:
        avg_notes_by_sample[i] = 29


for (i, sample) in enumerate(mod.samples):
    base_note = avg_notes_by_sample[i]
    if sample.length > 0:
        wave_data = [
            (v/128 if v < 128 else (v-256)/128)
            for v in sample.data
        ]
        base_freq = 11084 * 2**((base_note - 29)/12)
        wavetable = make_wavetable(wave_data, base_freq)
        for frame in wavetable:
            wavetable_data_buffer.write(frame.packed_data())

    block_count = len(wavetable)
    block_size = base_freq // 60
    sample_meta.append({
        'start': wavetable_data_length,
        'length': block_count,
        'repeat_from': wavetable_data_length + int(sample.repeat_from / block_size) * 18,
        'repeat_length': int(sample.repeat_length / block_size),
        'base_note': base_note,
    })
    wavetable_data_length += block_count * 18

sample_meta_string = ",\n".join([
    "{%d,%d,%d,%d,%d}" % (s['start'], s['length'], s['repeat_from'], s['repeat_length'], s['base_note'])
    for s in sample_meta
])

pattern_data_buffer = BytesIO()
for pattern in mod.patterns:
    for row in pattern:
        for (note, sample, effect, param) in row:
            pattern_data_buffer.write(bytes([255 if note is None else note, sample, effect, param]))

pattern_data = pattern_data_buffer.getvalue()
pattern_data_start_addr = 0x4000
sample_data_start_addr = pattern_data_start_addr + len(pattern_data)
mod_data = pattern_data + wavetable_data_buffer.getvalue()

print("Mod data: %d bytes (max: 49152)" % len(mod_data))


positions = "{%s}" % (",".join([
    str(v) for v in mod.positions[:mod.position_count]
]))

program_data = f'''-- title:  modplayer
-- author: Gasman / Hooy-Program
-- desc:   MOD playback on TIC-80
-- script: lua

-- 1=start address of sample (0-based)
-- 2=sample length in frames
-- 3=address to repeat from (0-based)
-- 4=repeat length in frames
-- 5=sample's native note frequency
samples_meta = {{
    {sample_meta_string}
}}

positions = {positions}

-- 1=sample address pointer
-- 2=sample number
-- 3=frames left until sample ends or repeats
-- 4=semitone shift from sample's native frequency
-- 5=vol multiplier
channel_states = {{
    {{0, 0, 0, 0, 0, 0}},
    {{0, 0, 0, 0, 0, 0}},
    {{0, 0, 0, 0, 0, 0}},
    {{0, 0, 0, 0, 0, 0}},
}}

t=0

row_duration = 1.2*6
next_row_time = 0
row_num = -1
position_num = 1
pattern_num = positions[1]
pattern_data_start_addr = {pattern_data_start_addr}
sample_data_start_addr = {sample_data_start_addr}

function play_frame()
  if next_row_time <= t then
    -- read new row
    row_num = row_num + 1

    if row_num == 64 then
      -- read new pattern
      row_num = 0
      position_num = (position_num % #positions) + 1
      pattern_num = positions[position_num]
    end

    pattern_addr = pattern_data_start_addr + pattern_num * 64 * 4 * 4
    row_addr = pattern_addr + row_num * 4 * 4
    for chan=1,4 do
      cell_addr = row_addr + (chan - 1) * 4
      note_num = peek(cell_addr)
      if note_num ~= 255 then
        sample_num = peek(cell_addr + 1)
        sample_meta=samples_meta[sample_num]
        channel_states[chan][1] = sample_meta[1]+sample_data_start_addr
        channel_states[chan][2] = sample_num
        channel_states[chan][3] = sample_meta[2]
        channel_states[chan][4] = note_num - sample_meta[5]
        effect = peek(cell_addr + 2)
        if effect == 0x0c then
          param = peek(cell_addr + 3)
          channel_states[chan][5] = param / 64
        else
          channel_states[chan][5] = 1
        end
        if effect == 0x0f then
          param = peek(cell_addr + 3)
          if param <= 32 then
            row_duration = 1.2*param
          else
            row_duration = 900/param
          end
        end
      end
    end
    -- advance next_row_time
    next_row_time = next_row_time + row_duration
  end

  for chan=1,4 do
    chan_addr = 0xff9c+(chan-1)*18
    state=channel_states[chan]
    if state[2] > 0 then
      sample_meta=samples_meta[state[2]]
      if state[3]==0 then
        -- sample end reached
        if sample_meta[4] > 0 then
          -- repeating
          state[1]=sample_meta[3]+sample_data_start_addr
          state[3]=sample_meta[4]
        else
          -- non-repeating - stop sample
          state[2]=0
        end
      end
    end

    if state[2]>0 then
      addr=state[1]
      b1=peek(addr)
      b2=peek(addr+1)
      addr = addr + 2
      wave_freq=b1|((b2 & 0x0f) << 8)
      freq=((wave_freq*(2^(state[4]/12)))+0.5)//1
      wave_vol=b2>>4
      vol=wave_vol*state[5]//1
      poke(chan_addr,freq&0xff)
      poke(chan_addr+1,(freq>>8)|(vol<<4))
      chan_addr = chan_addr + 2
      for i=0,15 do
        poke(chan_addr+i,peek(addr))
        addr = addr + 1
      end
      state[1]=addr
      state[3]=state[3] - 1
    else
      poke(chan_addr+1,0)
    end
  end

  t=t+1
end

scrolltext = "Hello! I'm Gasman, and you're listening to Guitarous by The Warlock / Grace... quite possibly the first MOD file I ever listened to, from some music collection that ran on a PC speaker (erk). So it seemed quite appropriate to borrow it for the world's first MOD player for TIC-80. The TIC-80 still doesn't do real sample playback, so this is a continuation of the technique from my Mercedes Benz demo: first, chop each sample into 1/60s slices, and do some statistical analysis on it to work out where the waveform repeats. (Which seems like it should be FFT, but... isn't, really.) Then take one of those waves, and squish it down into the 32 nibbles that the TIC-80 provides for a waveform on a single frame. Do that 60 times a second, and as long as that sample is something close to a musical note, we can play it back in a more or less recognisable form. Even better, we can pitch-shift it by altering the playback frequency... and combining that with the TIC-80's four channels, you've got everything you need for a MOD player. It's a very limited one, of course (not least since I've only been hacking on it over the Christmas holidays) - there's some hacky code to deal with drum samples, trying to work out when the pitch matching breaks down and ham-fistedly dropping some noise in instead. Hardly any effects are implemented right now, and we're also constrained by the TIC-80 memory map, so there's a 48Kb limit on the crunched sample and pattern data. Is this the future of music on the TIC-80? Probably not, assuming people don't want their soundtracks to sound like vacuum cleaners full of nails. But at the end of the day all we're really doing here is massively increasing the 16 waveforms that the native TIC-80 tracker gives us... I reckon that if we used this technique on original tracks written with the TIC-80 in mind, with some combination of sampled, synthesised and simple chip instruments - rather than expecting an automatic conversion to do a good job across the board - we could get some really nice-sounding results, and maybe even bless the TIC-80 with its very own 'signature sound'. That would be pretty awesome, wouldn't it? Maybe we can make 2023 the year that we really start pushing TIC-80 demos beyond the arena of size-coding :-) Greetings to Field-FX, Slipstream, TUHB, ScenePT All-Stars, Bitshifters, RBBS, Poo-Brain, MountainBytes, Marquee Design, pestis, ilmenit, dave84, p01 and my fellow Demozoo conspirators. Happy holidays and a prosperous 2023 to you all! Gasman signing off at the end of 2022...                        "

scroll_x = 0
scroll_next_char_index = 1
scroll_buffer = "                              "

function get_width(s)
  return print(s, 0, -100, 6, false, 2)
end

scroll_first_char_width = get_width(string.sub(scroll_buffer, 1, 1))
scroll_buffer_width = get_width(scroll_buffer)

function TIC()
  play_frame()
  cls()
  for chan=0,3 do
    addr = 0xff9c+chan*18
    a=0
    b1 = peek(addr)
    b2 = peek(addr+1)
    freq = b1 | ((b2 & 0x0f) << 8)
    ampl = b2 >> 4
    step = freq/480
    wave_addr = (addr + 2) * 2
    for x=0,239 do
      v = ampl * (peek4(wave_addr + a) - 7)
      pix(x, v/16 + chan*24 + 16, 4)
      a = (a + step) % 32
    end
  end

  if -scroll_x >= scroll_first_char_width then
    scroll_buffer = string.sub(scroll_buffer, 2)
    scroll_x = scroll_x + scroll_first_char_width
    scroll_buffer_width = scroll_buffer_width - scroll_first_char_width
    scroll_first_char_width = get_width(string.sub(scroll_buffer, 1, 1))
  end

  if scroll_buffer_width < 260 then
    next_char = string.sub(scrolltext, scroll_next_char_index, scroll_next_char_index)
    scroll_buffer_width = scroll_buffer_width + get_width(next_char)
    scroll_buffer = scroll_buffer .. next_char
    scroll_next_char_index = (scroll_next_char_index % #scrolltext) + 1
  end

  print(scroll_buffer, scroll_x, 110, 6, false, 2)
  scroll_x = scroll_x - 2
end
'''.encode('ascii')

# print(program_data)

chunks = [
    Chunk(ChunkType.CODE, 0, program_data),
    Chunk(ChunkType.DEFAULT, 0, b''),
    Chunk(ChunkType.TILES, 0, mod_data[0:0x2000]),
]
if len(mod_data) > 0x2000:
    chunks.append(Chunk(ChunkType.SPRITES, 0, mod_data[0x2000:0x4000]))
if len(mod_data) > 0x4000:
    chunks.append(Chunk(ChunkType.MAP, 0, mod_data[0x4000:0xc000]))

tic = TICFile(chunks)
tic.save("modplayer.tic")
