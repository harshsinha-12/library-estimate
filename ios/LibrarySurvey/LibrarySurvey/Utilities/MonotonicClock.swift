import Foundation

enum MonotonicClock {
  static var now: Double { ProcessInfo.processInfo.systemUptime }
}
