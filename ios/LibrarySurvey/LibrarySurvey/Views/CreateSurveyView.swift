import SwiftUI

struct CreateSurveyView: View {
  @ObservedObject var store: SurveyDraftStore
  @ObservedObject var location: LocationService
  let onContinue: () -> Void

  var body: some View {
    Form {
      Section("Survey") {
        TextField("Library or property name", text: $store.draft.displayName)
          .textContentType(.organizationName)
      }

      Section("Consent") {
        Toggle("Capture camera evidence", isOn: $store.draft.consent.video)
        Toggle("Record spoken notes", isOn: $store.draft.consent.audio)
        Toggle("Use location once", isOn: $store.draft.consent.location)
        Toggle(
          "Store precise coordinates",
          isOn: $store.draft.geography.preciseLocationConsent
        )
        .disabled(!store.draft.consent.location)
      }

      Section("Survey geography") {
        if store.draft.consent.location {
          Button("Use Current Location", systemImage: "location") {
            location.requestOneReading(
              preciseLocationConsent: store.draft.geography.preciseLocationConsent
            )
          }
          locationStatus
        }
        TextField("Country code", text: $store.draft.geography.countryCode)
          .textInputAutocapitalization(.characters)
        TextField("Region (optional)", text: optional($store.draft.geography.region))
        TextField("City", text: $store.draft.geography.city)
        TextField("Currency", text: $store.draft.geography.currency)
          .textInputAutocapitalization(.characters)
        TextField("Market, for example en-IN", text: $store.draft.geography.market)
          .textInputAutocapitalization(.never)
        Text("If location is denied or unavailable, country and city remain mandatory.")
          .font(.caption)
          .foregroundStyle(.secondary)
      }

      Section {
        Button("Continue to Device Check", action: continueWithValidatedGeography)
          .disabled(!canContinue)
      }
    }
    .onChange(of: location.suggestedGeography) { _, geography in
      guard let geography else { return }
      store.draft.geography = geography
    }
  }

  @ViewBuilder
  private var locationStatus: some View {
    switch location.state {
    case .idle:
      EmptyView()
    case .requesting:
      ProgressView("Resolving country and city")
    case .resolved:
      Label("Location resolved; fields remain editable", systemImage: "checkmark.circle")
        .foregroundStyle(.green)
    case .denied:
      Label("Location denied—enter country and city manually", systemImage: "hand.raised")
        .foregroundStyle(.orange)
    case let .failed(message):
      Text("Location unavailable: \(message). Enter it manually.")
        .foregroundStyle(.orange)
    }
  }

  private var canContinue: Bool {
    !store.draft.displayName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
      && !store.draft.geography.countryCode.isEmpty
      && !store.draft.geography.city.isEmpty
      && store.draft.consent.video
      && store.draft.consent.audio
  }

  private func continueWithValidatedGeography() {
    store.draft.geography.countryCode = store.draft.geography.countryCode.uppercased()
    let defaults = AppConfiguration.localeDefaults(
      countryCode: store.draft.geography.countryCode
    )
    if store.draft.geography.currency.isEmpty {
      store.draft.geography.currency = defaults.currency
    }
    if store.draft.geography.market.isEmpty {
      store.draft.geography.market = defaults.market
    }
    if location.suggestedGeography == nil {
      store.draft.geography.source = .manual
      store.draft.geography.latitude = nil
      store.draft.geography.longitude = nil
    } else if store.draft.geography != location.suggestedGeography {
      store.draft.geography.source = .mixed
    }
    onContinue()
  }

  private func optional(_ binding: Binding<String?>) -> Binding<String> {
    Binding(
      get: { binding.wrappedValue ?? "" },
      set: { binding.wrappedValue = $0.isEmpty ? nil : $0 }
    )
  }
}

