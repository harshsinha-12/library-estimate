import SwiftUI
import UIKit

private struct MinimumScaledTouchTarget: ViewModifier {
  @ScaledMetric(relativeTo: .body) private var minimumSize: CGFloat = 44

  func body(content: Content) -> some View {
    content
      .frame(minWidth: max(44, minimumSize), minHeight: max(44, minimumSize))
      .contentShape(Rectangle())
  }
}

extension View {
  /// Keeps interactive controls at least 44 points and grows them with Dynamic Type.
  func minimumScaledTouchTarget() -> some View {
    modifier(MinimumScaledTouchTarget())
  }

  /// Announces meaningful asynchronous or capture-state changes without moving focus.
  func accessibilityStatusAnnouncements(_ status: String) -> some View {
    onChange(of: status) { oldValue, newValue in
      guard !newValue.isEmpty, newValue != oldValue else { return }
      UIAccessibility.post(notification: .announcement, argument: newValue)
    }
  }
}

struct AccessibleStatusLabel: View {
  enum Kind {
    case success
    case warning
    case error
    case progress
    case neutral

    var icon: String {
      switch self {
      case .success: "checkmark.circle.fill"
      case .warning: "exclamationmark.triangle.fill"
      case .error: "xmark.octagon.fill"
      case .progress: "clock.arrow.circlepath"
      case .neutral: "info.circle.fill"
      }
    }

    var color: Color {
      switch self {
      case .success: .green
      case .warning: .orange
      case .error: .red
      case .progress: .blue
      case .neutral: .secondary
      }
    }
  }

  let text: String
  let kind: Kind

  var body: some View {
    Label(text, systemImage: kind.icon)
      .foregroundStyle(kind.color)
      .fixedSize(horizontal: false, vertical: true)
      .accessibilityElement(children: .combine)
      .accessibilityLabel(text)
  }
}
