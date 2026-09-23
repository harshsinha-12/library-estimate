import PhotosUI
import SwiftUI
import UIKit

struct ShelfPhoto: Identifiable, Equatable {
  let id: UUID
  var jpeg: Data
  var thumbnail: UIImage

  static func == (lhs: ShelfPhoto, rhs: ShelfPhoto) -> Bool {
    lhs.id == rhs.id && lhs.jpeg == rhs.jpeg
  }
}

struct ShelfPhotoGroup: Identifiable, Equatable {
  let id: UUID
  var name: String
  var photos: [ShelfPhoto]

  static func == (lhs: ShelfPhotoGroup, rhs: ShelfPhotoGroup) -> Bool {
    lhs.id == rhs.id && lhs.name == rhs.name && lhs.photos == rhs.photos
  }
}

struct ShelfPhotoManifest: Encodable {
  struct Shelf: Encodable {
    let shelfId: String
    let label: String
    let photos: [String]
  }

  let schemaVersion: String
  let shelves: [Shelf]
}

struct ShelfPhotoExport {
  let manifest: ShelfPhotoManifest
  let files: [String: Data]
}

@MainActor
final class ShelfPhotoStore: ObservableObject {
  @Published var shelves: [ShelfPhotoGroup]
  @Published var activeID: UUID

  init() {
    let first = ShelfPhotoGroup(id: UUID(), name: "Shelf 1", photos: [])
    shelves = [first]
    activeID = first.id
  }

  var activeShelf: ShelfPhotoGroup? {
    shelves.first { $0.id == activeID }
  }

  var photoCount: Int {
    shelves.reduce(0) { $0 + $1.photos.count }
  }

  func select(_ id: UUID) {
    activeID = id
  }

  func renameActive(_ name: String) {
    guard let index = shelves.firstIndex(where: { $0.id == activeID }) else { return }
    shelves[index].name = name
  }

  func addShelf() {
    let next = ShelfPhotoGroup(id: UUID(), name: "Shelf \(shelves.count + 1)", photos: [])
    shelves.append(next)
    activeID = next.id
  }

  func add(_ image: UIImage) {
    guard let jpeg = Self.jpegData(from: image),
          let thumbnail = UIImage(data: jpeg),
          let index = shelves.firstIndex(where: { $0.id == activeID })
    else { return }
    shelves[index].photos.append(ShelfPhoto(id: UUID(), jpeg: jpeg, thumbnail: thumbnail))
  }

  func attach(_ items: [PhotosPickerItem]) async {
    var images: [UIImage] = []
    for item in items {
      if let data = try? await item.loadTransferable(type: Data.self),
         let image = UIImage(data: data) {
        images.append(image)
      }
    }
    for image in images {
      add(image)
    }
  }

  func removePhoto(_ photoID: UUID) {
    guard let index = shelves.firstIndex(where: { $0.id == activeID }) else { return }
    shelves[index].photos.removeAll { $0.id == photoID }
  }

  func export() -> ShelfPhotoExport {
    var records: [ShelfPhotoManifest.Shelf] = []
    var files: [String: Data] = [:]
    for (shelfIndex, shelf) in shelves.enumerated() where !shelf.photos.isEmpty {
      let shelfId = String(format: "shelf_%02d", shelfIndex + 1)
      var paths: [String] = []
      for (photoIndex, photo) in shelf.photos.enumerated() {
        let path = String(format: "shelf_photos/%@/photo_%02d.jpg", shelfId, photoIndex + 1)
        paths.append(path)
        files[path] = photo.jpeg
      }
      let label = shelf.name.trimmingCharacters(in: .whitespacesAndNewlines)
      records.append(
        ShelfPhotoManifest.Shelf(
          shelfId: shelfId,
          label: label.isEmpty ? "Shelf \(shelfIndex + 1)" : label,
          photos: paths
        )
      )
    }
    return ShelfPhotoExport(
      manifest: ShelfPhotoManifest(schemaVersion: "shelf-photos-v1", shelves: records),
      files: files
    )
  }

  func reset() {
    let first = ShelfPhotoGroup(id: UUID(), name: "Shelf 1", photos: [])
    shelves = [first]
    activeID = first.id
  }

  static func jpegData(from image: UIImage) -> Data? {
    let maxEdge: CGFloat = 1600
    let size = image.size
    let longest = max(size.width, size.height)
    let scale = longest > 0 ? min(1, maxEdge / longest) : 1
    let target = CGSize(width: max(1, size.width * scale), height: max(1, size.height * scale))
    let format = UIGraphicsImageRendererFormat()
    format.scale = 1
    let rendered = UIGraphicsImageRenderer(size: target, format: format).image { _ in
      image.draw(in: CGRect(origin: .zero, size: target))
    }
    return rendered.jpegData(compressionQuality: 0.72)
  }
}
