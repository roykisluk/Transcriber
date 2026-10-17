// meeting-capture — stream the "Meeting Input" aggregate to stdout as raw
// interleaved float32 PCM, at the device's own rate and channel count.
//
//   meeting-capture --format   prints "<rate> <channels>" and exits
//   meeting-capture            streams until stdout closes (ffmpeg exits)
//
// Replaces ffmpeg's avfoundation input, which loses ~10% of samples: it keeps a
// single buffer slot that each new CoreAudio buffer overwrites, and polls it
// every 10 ms (really ~11.9 ms) while 48 kHz buffers arrive every 10.67 ms.
// AudioQueue holds several 100 ms buffers instead, so a slow reader only delays.
// Any buffer that is still lost shows up as a sample-time gap, reported on stderr.
//
// Build: swiftc -O -framework CoreAudio -framework AudioToolbox -o meeting-capture meeting-capture.swift

import Foundation
import CoreAudio
import AudioToolbox

let DEVICE_UID = "com.roybox.meeting.input"   // = INPUT_UID in meeting-devices.swift

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write(Data(("meeting-capture: " + msg + "\n").utf8))
    exit(1)
}

func deviceByUID(_ uid: String) -> AudioObjectID? {
    var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyTranslateUIDToDevice,
                                          mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    var cfUID = uid as CFString
    var dev = AudioObjectID(0)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    let st = withUnsafePointer(to: &cfUID) {
        AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr,
                                   UInt32(MemoryLayout<CFString>.size), $0, &size, &dev)
    }
    return st == noErr && dev != 0 ? dev : nil
}

guard let dev = deviceByUID(DEVICE_UID) else { fail("Meeting Input not found (run meeting-devices)") }

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
guard channels > 0 else { fail("Meeting Input has no input channels") }

if CommandLine.arguments.dropFirst().first == "--format" {
    print("\(Int(rate)) \(channels)")
    exit(0)
}

var fmt = AudioStreamBasicDescription(
    mSampleRate: rate, mFormatID: kAudioFormatLinearPCM,
    mFormatFlags: kLinearPCMFormatFlagIsFloat | kLinearPCMFormatFlagIsPacked,
    mBytesPerPacket: 4 * channels, mFramesPerPacket: 1, mBytesPerFrame: 4 * channels,
    mChannelsPerFrame: channels, mBitsPerChannel: 32, mReserved: 0)

signal(SIGPIPE, SIG_IGN)   // a closed stdout is the stop signal: handled as EPIPE below
var nextSample: Float64 = -1
var lostFrames: Float64 = 0
var queue: AudioQueueRef?
let cbQueue = DispatchQueue(label: "meeting-capture")

func finish() -> Never {
    if lostFrames > 0 {
        fail(String(format: "lost %.0f frames (%.2f s) to overruns", lostFrames, lostFrames / rate))
    }
    exit(0)
}

let st = AudioQueueNewInputWithDispatchQueue(&queue, &fmt, 0, cbQueue) { q, buf, start, _, _ in
    if start.pointee.mFlags.contains(.sampleTimeValid) {
        if nextSample >= 0, start.pointee.mSampleTime > nextSample + 1 {
            lostFrames += start.pointee.mSampleTime - nextSample
        }
        nextSample = start.pointee.mSampleTime + Float64(buf.pointee.mAudioDataByteSize / fmt.mBytesPerFrame)
    }
    var p = buf.pointee.mAudioData
    var left = Int(buf.pointee.mAudioDataByteSize)
    while left > 0 {
        let n = write(1, p, left)
        if n < 0 { if errno == EINTR { continue }; finish() }   // reader gone
        p += n; left -= n
    }
    AudioQueueEnqueueBuffer(q, buf, 0, nil)
}
guard st == noErr, let q = queue else { fail("AudioQueueNewInput failed (OSStatus \(st))") }

var uid = DEVICE_UID as CFString
guard withUnsafePointer(to: &uid, {
    AudioQueueSetProperty(q, kAudioQueueProperty_CurrentDevice, $0, UInt32(MemoryLayout<CFString>.size))
}) == noErr else { fail("could not select Meeting Input") }

// 8 x 100 ms: the reader can stall ~0.7 s before anything is lost.
let bufBytes = UInt32(rate / 10) * fmt.mBytesPerFrame
for _ in 0..<8 {
    var b: AudioQueueBufferRef?
    guard AudioQueueAllocateBuffer(q, bufBytes, &b) == noErr, let b else { fail("buffer alloc failed") }
    AudioQueueEnqueueBuffer(q, b, 0, nil)
}
let started = AudioQueueStart(q, nil)
guard started == noErr else { fail("AudioQueueStart failed (OSStatus \(started))") }
dispatchMain()
