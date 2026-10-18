// meeting-capture — stream mic + everything the Mac plays to stdout as raw
// interleaved float32 PCM, at the mic's own rate: mic channels first, then the
// 2 channels of a system-wide process tap.
//
//   meeting-capture --format   prints "<rate> <channels>" and exits
//   meeting-capture            streams until stdout closes (ffmpeg exits)
//
// The far end comes from a CoreAudio process tap, not BlackHole + a multi-output
// device: the tap hears every app whatever output device it picked, so a WhatsApp
// call that ignores the default output is still captured (lesson log 2026-10-18).
// The tap needs "System Audio Recording" for the terminal (iTerm); without it the
// tap delivers silence, so the grant is checked up front and warned about.
//
// Read with an IOProc. An AudioQueue on the tap aggregate got all-zero tap
// channels, and ffmpeg's avfoundation input loses ~10% of samples (2026-10-17).
// The IOProc only copies; writes happen on a serial queue, so a slow reader
// delays output instead of dropping input. A sample-time gap means lost input
// and is reported on stderr.
//
// Build: swiftc -O -framework CoreAudio -framework AudioToolbox -o meeting-capture meeting-capture.swift

import Foundation
import CoreAudio
import AudioToolbox

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write(Data(("meeting-capture: " + msg + "\n").utf8))
    exit(1)
}

func address(_ sel: AudioObjectPropertySelector) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: sel, mScope: kAudioObjectPropertyScopeGlobal,
                               mElement: kAudioObjectPropertyElementMain)
}

func defaultInputUID() -> String? {
    var addr = address(kAudioHardwarePropertyDefaultInputDevice)
    var mic = AudioObjectID(0)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &mic) == noErr,
          mic != 0 else { return nil }
    addr = address(kAudioDevicePropertyDeviceUID)
    var uid: Unmanaged<CFString>?
    size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    guard AudioObjectGetPropertyData(mic, &addr, 0, nil, &size, &uid) == noErr else { return nil }
    return uid?.takeRetainedValue() as String?
}

// Far end = a process tap on everything the Mac plays, whatever device each app
// sends it to (WhatsApp ignores the default output once a call is up). The tap
// and the mic form a private aggregate that lives only as long as this process:
// mic channels first, the tap's 2 channels last, as transcribe-meeting expects.
// TCCAccessPreflight is private API (as AudioCap uses it): 0 = granted.
// A missing grant still records the mic, so warn instead of failing.
if let tcc = dlopen("/System/Library/PrivateFrameworks/TCC.framework/Versions/A/TCC", RTLD_NOW),
   let sym = dlsym(tcc, "TCCAccessPreflight") {
    typealias Preflight = @convention(c) (CFString, CFDictionary?) -> Int
    if unsafeBitCast(sym, to: Preflight.self)("kTCCServiceAudioCapture" as CFString, nil) != 0 {
        FileHandle.standardError.write(Data(("meeting-capture: WARNING: no System Audio Recording permission, " +
            "the other side will be SILENT. Grant it: System Settings > Privacy & Security > " +
            "Screen & System Audio Recording > System Audio Recording Only > + iTerm, then restart iTerm.\n").utf8))
    }
}

guard let micUID = defaultInputUID() else { fail("no default input device") }
let tapDesc = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
tapDesc.isPrivate = true
var tap = AudioObjectID(0)
let tapSt = AudioHardwareCreateProcessTap(tapDesc, &tap)
guard tapSt == noErr else { fail("could not create the system audio tap (OSStatus \(tapSt))") }
let aggUID = UUID().uuidString
let aggDesc: [String: Any] = [
    "name": "Meeting Capture", "uid": aggUID, "private": true, "stacked": false,
    "master": micUID, "tapautostart": true,
    "subdevices": [["uid": micUID]],
    "taps": [["uid": tapDesc.uuid.uuidString, "drift": true]],
]
var dev = AudioObjectID(0)
let aggSt = AudioHardwareCreateAggregateDevice(aggDesc as CFDictionary, &dev)
guard aggSt == noErr else { fail("could not create the capture device (OSStatus \(aggSt))") }

var rateAddr = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyNominalSampleRate,
                                          mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
var rate = Float64(0)
var size = UInt32(MemoryLayout<Float64>.size)
guard AudioObjectGetPropertyData(dev, &rateAddr, 0, nil, &size, &rate) == noErr, rate > 0
else { fail("could not read the sample rate") }

var cfgAddr = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreamConfiguration,
                                         mScope: kAudioObjectPropertyScopeInput,
                                         mElement: kAudioObjectPropertyElementMain)
guard AudioObjectGetPropertyDataSize(dev, &cfgAddr, 0, nil, &size) == noErr else { fail("no stream config") }
let cfg = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: 16)
guard AudioObjectGetPropertyData(dev, &cfgAddr, 0, nil, &size, cfg) == noErr else { fail("no stream config") }
let channels = UnsafeMutableAudioBufferListPointer(cfg.assumingMemoryBound(to: AudioBufferList.self))
    .reduce(UInt32(0)) { $0 + $1.mNumberChannels }
guard channels > 0 else { fail("capture device has no input channels") }

if CommandLine.arguments.dropFirst().first == "--format" {
    print("\(Int(rate)) \(channels)")
    exit(0)
}

signal(SIGPIPE, SIG_IGN)   // a closed stdout is the stop signal: handled as EPIPE below
var nextSample: Float64 = -1
var lostFrames: Float64 = 0
let writer = DispatchQueue(label: "meeting-capture")

func finish() -> Never {
    if lostFrames > 0 {
        fail(String(format: "lost %.0f frames (%.2f s) to overruns", lostFrames, lostFrames / rate))
    }
    exit(0)
}

// The aggregate delivers one buffer per stream (mic, tap), each interleaved on
// its own; weave them into one interleaved frame stream.
var ioProc: AudioDeviceIOProcID?
let ioSt = AudioDeviceCreateIOProcIDWithBlock(&ioProc, dev, nil) { _, input, inTime, _, _ in
    let bufs = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: input))
    guard let first = bufs.first, first.mNumberChannels > 0 else { return }
    let frames = Int(first.mDataByteSize) / 4 / Int(first.mNumberChannels)
    if inTime.pointee.mFlags.contains(.sampleTimeValid) {
        if nextSample >= 0, inTime.pointee.mSampleTime > nextSample + 1 {
            lostFrames += inTime.pointee.mSampleTime - nextSample
        }
        nextSample = inTime.pointee.mSampleTime + Float64(frames)
    }
    let nch = Int(channels)
    // ponytail: allocates on the IO thread; a preallocated ring buffer if glitches ever show up.
    var out = Data(count: frames * nch * 4)
    out.withUnsafeMutableBytes { raw in
        let dst = raw.bindMemory(to: Float.self)
        var c0 = 0
        for b in bufs {
            let n = Int(b.mNumberChannels)
            if let src = b.mData?.assumingMemoryBound(to: Float.self) {
                for f in 0..<min(frames, Int(b.mDataByteSize) / 4 / max(n, 1)) {
                    for k in 0..<n { dst[f * nch + c0 + k] = src[f * n + k] }
                }
            }
            c0 += n
        }
    }
    writer.async {
        out.withUnsafeBytes { raw in
            var p = raw.baseAddress!
            var left = raw.count
            while left > 0 {
                let n = write(1, p, left)
                if n < 0 { if errno == EINTR { continue }; finish() }   // reader gone
                p += n; left -= n
            }
        }
    }
}
guard ioSt == noErr, let ioProc else { fail("could not attach to the capture device (OSStatus \(ioSt))") }
// Keep-alive: the tap stops whenever nothing on the Mac is playing, and it
// stalls the whole aggregate, mic included (2026-10-18: zero callbacks until a
// sound played). So play silence to the default output for as long as we run.
var silentFmt = AudioStreamBasicDescription(
    mSampleRate: 48000, mFormatID: kAudioFormatLinearPCM,
    mFormatFlags: kLinearPCMFormatFlagIsFloat | kLinearPCMFormatFlagIsPacked,
    mBytesPerPacket: 8, mFramesPerPacket: 1, mBytesPerFrame: 8,
    mChannelsPerFrame: 2, mBitsPerChannel: 32, mReserved: 0)
var silent: AudioQueueRef?
guard AudioQueueNewOutputWithDispatchQueue(&silent, &silentFmt, 0, DispatchQueue(label: "silence"), { q, buf in
    memset(buf.pointee.mAudioData, 0, Int(buf.pointee.mAudioDataBytesCapacity))
    buf.pointee.mAudioDataByteSize = buf.pointee.mAudioDataBytesCapacity
    AudioQueueEnqueueBuffer(q, buf, 0, nil)
}) == noErr, let silent else { fail("could not start the keep-alive output") }
for _ in 0..<3 {
    var b: AudioQueueBufferRef?
    guard AudioQueueAllocateBuffer(silent, 4800 * 8, &b) == noErr, let b else { fail("buffer alloc failed") }
    memset(b.pointee.mAudioData, 0, Int(b.pointee.mAudioDataBytesCapacity))
    b.pointee.mAudioDataByteSize = b.pointee.mAudioDataBytesCapacity
    AudioQueueEnqueueBuffer(silent, b, 0, nil)
}
guard AudioQueueStart(silent, nil) == noErr else { fail("could not start the keep-alive output") }

let started = AudioDeviceStart(dev, ioProc)
guard started == noErr else { fail("could not start capture (OSStatus \(started))") }
dispatchMain()
