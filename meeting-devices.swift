// meeting-devices — create/refresh the "Meeting Input" and "Meeting Output"
// CoreAudio aggregate devices used by `record` to capture mic + system audio.
//
// "Meeting Input"  = aggregate( default input mic + BlackHole 2ch ), master = mic.
// "Meeting Output" = stacked aggregate( default output + BlackHole 2ch ), master = output.
// Drift correction is applied to the BlackHole sub-device in both.
//
// Idempotent: if an aggregate already exists with the correct sub-devices it is
// left alone; if the underlying default device changed (e.g. switched to AirPods)
// it is destroyed and recreated. Aborts with a clear message if BlackHole is
// missing or if a system default IS BlackHole itself.
//
// Build: swiftc -O -framework CoreAudio -framework AudioToolbox -o meeting-devices meeting-devices.swift

import Foundation
import CoreAudio

// Aggregate/sub-device dictionary keys. These are C string-literal macros that
// Swift does not import; the values are stable public API (persisted in device
// configs, so Apple cannot change them).
let kNameKey     = "name"        // kAudioAggregateDeviceNameKey / kAudioSubDeviceNameKey
let kUIDKey      = "uid"         // kAudioAggregateDeviceUIDKey  / kAudioSubDeviceUIDKey
let kSubListKey  = "subdevices"  // kAudioAggregateDeviceSubDeviceListKey
let kMasterKey   = "master"      // kAudioAggregateDeviceMasterSubDeviceKey
let kStackedKey  = "stacked"     // kAudioAggregateDeviceIsStackedKey
let kDriftKey    = "drift"       // kAudioSubDeviceDriftCompensationKey

// Our fixed aggregate identities.
let INPUT_UID  = "com.roybox.meeting.input"
let OUTPUT_UID = "com.roybox.meeting.output"
let INPUT_NAME  = "Meeting Input"
let OUTPUT_NAME = "Meeting Output"

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write(Data(("meeting-devices: " + msg + "\n").utf8))
    exit(1)
}

let systemObject = AudioObjectID(kAudioObjectSystemObject)

func address(_ selector: AudioObjectPropertySelector) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: selector,
                               mScope: kAudioObjectPropertyScopeGlobal,
                               mElement: kAudioObjectPropertyElementMain)
}

// Read a CFString property (device UID, device name) as a Swift String.
func stringProp(_ obj: AudioObjectID, _ selector: AudioObjectPropertySelector) -> String? {
    var addr = address(selector)
    var out: Unmanaged<CFString>?
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    let st = AudioObjectGetPropertyData(obj, &addr, 0, nil, &size, &out)
    guard st == noErr, let cf = out?.takeRetainedValue() else { return nil }
    return cf as String
}

// Read an aggregate device's full sub-device UID list.
func subDeviceUIDs(_ dev: AudioObjectID) -> [String] {
    var addr = address(kAudioAggregateDevicePropertyFullSubDeviceList)
    var size = UInt32(0)
    guard AudioObjectGetPropertyDataSize(dev, &addr, 0, nil, &size) == noErr, size > 0 else { return [] }
    var out: Unmanaged<CFArray>?
    guard AudioObjectGetPropertyData(dev, &addr, 0, nil, &size, &out) == noErr,
          let arr = out?.takeRetainedValue() as? [String] else { return [] }
    return arr
}

func allDevices() -> [AudioObjectID] {
    var addr = address(kAudioHardwarePropertyDevices)
    var size = UInt32(0)
    guard AudioObjectGetPropertyDataSize(systemObject, &addr, 0, nil, &size) == noErr else { return [] }
    let count = Int(size) / MemoryLayout<AudioObjectID>.size
    var devices = [AudioObjectID](repeating: 0, count: count)
    guard AudioObjectGetPropertyData(systemObject, &addr, 0, nil, &size, &devices) == noErr else { return [] }
    return devices
}

func defaultDevice(_ selector: AudioObjectPropertySelector) -> AudioObjectID {
    var addr = address(selector)
    var dev = AudioObjectID(0)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    if AudioObjectGetPropertyData(systemObject, &addr, 0, nil, &size, &dev) != noErr {
        fail("could not read the current default device")
    }
    return dev
}

func deviceByUID(_ target: String) -> AudioObjectID? {
    allDevices().first { stringProp($0, kAudioDevicePropertyDeviceUID) == target }
}

// Locate BlackHole by name or UID.
func findBlackHoleUID() -> String? {
    for d in allDevices() {
        let name = stringProp(d, kAudioObjectPropertyName) ?? ""
        let uid  = stringProp(d, kAudioDevicePropertyDeviceUID) ?? ""
        if name.localizedCaseInsensitiveContains("blackhole")
            || uid.localizedCaseInsensitiveContains("blackhole") {
            return uid.isEmpty ? nil : uid
        }
    }
    return nil
}

// Resolve the real underlying device UID for a system default, handling the
// two stale-state edge cases from the brief.
func resolveRealUID(_ defaultDev: AudioObjectID, role: String, blackHoleUID: String) -> String {
    guard let uid = stringProp(defaultDev, kAudioDevicePropertyDeviceUID) else {
        fail("could not read UID of the default \(role) device")
    }
    if uid == blackHoleUID {
        fail("the default \(role) is BlackHole itself. Set a real \(role) device "
             + "(e.g. your built-in mic/speakers) as the system default, then rerun.")
    }
    if uid == INPUT_UID || uid == OUTPUT_UID {
        // Stale: the default is one of our aggregates. Use its non-BlackHole member.
        if let real = subDeviceUIDs(defaultDev).first(where: { $0 != blackHoleUID }) {
            return real
        }
        fail("the default \(role) is a stale Meeting aggregate with no real sub-device; "
             + "reset your default \(role) device and rerun.")
    }
    return uid
}

func createAggregate(name: String, uid: String, masterUID: String, blackHoleUID: String, stacked: Bool) {
    var desc: [String: Any] = [
        kNameKey: name,
        kUIDKey: uid,
        kMasterKey: masterUID,
        kSubListKey: [
            [kUIDKey: masterUID],
            [kUIDKey: blackHoleUID, kDriftKey: 1],   // drift-correct BlackHole
        ],
    ]
    if stacked { desc[kStackedKey] = 1 }
    var newID = AudioObjectID(0)
    let st = AudioHardwareCreateAggregateDevice(desc as CFDictionary, &newID)
    if st != noErr { fail("failed to create \(name) (OSStatus \(st))") }
}

// Create the aggregate, or leave it if its sub-devices already match.
func ensureAggregate(name: String, uid: String, masterUID: String, blackHoleUID: String, stacked: Bool) {
    let desired = Set([masterUID, blackHoleUID])
    if let existing = deviceByUID(uid) {
        if Set(subDeviceUIDs(existing)) == desired {
            print("\(name): up to date")
            return
        }
        if AudioHardwareDestroyAggregateDevice(existing) != noErr {
            fail("failed to destroy stale \(name)")
        }
    }
    createAggregate(name: name, uid: uid, masterUID: masterUID, blackHoleUID: blackHoleUID, stacked: stacked)
    print("\(name): created")
}

// New aggregates can take a moment to become visible to other processes.
func waitVisible(_ uid: String, _ name: String) {
    for _ in 0..<30 {
        if deviceByUID(uid) != nil { return }
        usleep(100_000)
    }
    fail("\(name) did not become visible after creation")
}

// --- main ---------------------------------------------------------------

guard let blackHoleUID = findBlackHoleUID() else {
    fail("BlackHole 2ch not found. Install it with:  brew install --cask blackhole-2ch")
}

let micUID = resolveRealUID(defaultDevice(kAudioHardwarePropertyDefaultInputDevice),
                            role: "input", blackHoleUID: blackHoleUID)
let outUID = resolveRealUID(defaultDevice(kAudioHardwarePropertyDefaultOutputDevice),
                            role: "output", blackHoleUID: blackHoleUID)

ensureAggregate(name: INPUT_NAME,  uid: INPUT_UID,  masterUID: micUID, blackHoleUID: blackHoleUID, stacked: false)
ensureAggregate(name: OUTPUT_NAME, uid: OUTPUT_UID, masterUID: outUID, blackHoleUID: blackHoleUID, stacked: true)

waitVisible(INPUT_UID, INPUT_NAME)
waitVisible(OUTPUT_UID, OUTPUT_NAME)
