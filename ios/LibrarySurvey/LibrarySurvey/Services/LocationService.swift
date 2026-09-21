import Combine
import CoreLocation
import Foundation

@MainActor
final class LocationService: NSObject, ObservableObject, CLLocationManagerDelegate {
  enum State: Equatable {
    case idle
    case requesting
    case resolved
    case denied
    case failed(String)
  }

  @Published private(set) var state: State = .idle
  @Published private(set) var suggestedGeography: SurveyGeography?

  private let manager = CLLocationManager()
  private let geocoder = CLGeocoder()

  override init() {
    super.init()
    manager.delegate = self
    manager.desiredAccuracy = kCLLocationAccuracyKilometer
  }

  func requestOneReading(preciseLocationConsent: Bool) {
    state = .requesting
    manager.desiredAccuracy = preciseLocationConsent
      ? kCLLocationAccuracyBest
      : kCLLocationAccuracyThreeKilometers
    switch manager.authorizationStatus {
    case .authorizedAlways, .authorizedWhenInUse:
      manager.requestLocation()
    case .denied, .restricted:
      state = .denied
    case .notDetermined:
      manager.requestWhenInUseAuthorization()
    @unknown default:
      state = .failed("Unknown location authorization state.")
    }
  }

  nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
    Task { @MainActor in
      switch manager.authorizationStatus {
      case .authorizedAlways, .authorizedWhenInUse:
        manager.requestLocation()
      case .denied, .restricted:
        state = .denied
      case .notDetermined:
        break
      @unknown default:
        state = .failed("Unknown location authorization state.")
      }
    }
  }

  nonisolated func locationManager(
    _ manager: CLLocationManager,
    didUpdateLocations locations: [CLLocation]
  ) {
    guard let location = locations.last else { return }
    Task { @MainActor in reverseGeocode(location) }
  }

  nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
    Task { @MainActor in state = .failed(error.localizedDescription) }
  }

  private func reverseGeocode(_ location: CLLocation) {
    geocoder.reverseGeocodeLocation(location) { [weak self] placemarks, error in
      Task { @MainActor in
        guard let self else { return }
        if let error {
          self.state = .failed(error.localizedDescription)
          return
        }
        guard let placemark = placemarks?.first,
              let countryCode = placemark.isoCountryCode,
              let city = placemark.locality ?? placemark.subAdministrativeArea
        else {
          self.state = .failed(
            "Location was found, but country and city could not be resolved."
          )
          return
        }
        let defaults = AppConfiguration.localeDefaults(countryCode: countryCode)
        let precise = self.manager.desiredAccuracy == kCLLocationAccuracyBest
        self.suggestedGeography = SurveyGeography(
          countryCode: countryCode.uppercased(),
          region: placemark.administrativeArea,
          city: city,
          currency: defaults.currency,
          market: defaults.market,
          source: .gps,
          latitude: precise ? location.coordinate.latitude : nil,
          longitude: precise ? location.coordinate.longitude : nil,
          preciseLocationConsent: precise
        )
        self.state = .resolved
      }
    }
  }
}
