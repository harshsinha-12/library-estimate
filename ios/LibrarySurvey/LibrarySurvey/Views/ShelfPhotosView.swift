import PhotosUI
import SwiftUI
import UIKit

struct ShelfPhotosView: View {
  @ObservedObject var store: ShelfPhotoStore
  @ObservedObject var camera: CameraSessionCoordinator
  let sealError: String?
  let onSeal: () -> Void
  @State private var showCamera = false
  @State private var pickerItems: [PhotosPickerItem] = []
  @State private var cameraError: String?

  var body: some View {
    List {
      Section {
        Text(
          "Photograph one shelf, then the next. Each photo is sent to a language model, which names the books it can read, estimates the count, looks up prices, and totals the shelf."
        )
        .font(.subheadline)
        Text("Each photo is counted on its own. Photograph a different part of the shelf, not the same books again.")
          .font(.caption)
          .foregroundStyle(.secondary)
      }

      Section("Shelves") {
        ForEach(store.shelves) { shelf in
          Button {
            store.select(shelf.id)
          } label: {
            HStack {
              Text(shelf.name)
              Spacer()
              Text("\(shelf.photos.count)")
                .foregroundStyle(.secondary)
              if shelf.id == store.activeID {
                Image(systemName: "checkmark")
                  .foregroundStyle(.tint)
              }
            }
          }
          .minimumScaledTouchTarget()
          .accessibilityLabel(shelf.name)
          .accessibilityValue("\(shelf.photos.count) photos")
          .accessibilityAddTraits(shelf.id == store.activeID ? .isSelected : [])
        }
        Button("Next shelf", systemImage: "plus") {
          store.addShelf()
        }
        .minimumScaledTouchTarget()
      }

      if let shelf = store.activeShelf {
        Section(shelf.name) {
          TextField("Shelf name", text: nameBinding)
            .textInputAutocapitalization(.words)
          if shelf.photos.isEmpty {
            Text("No photos yet.")
              .foregroundStyle(.secondary)
          } else {
            ScrollView(.horizontal) {
              HStack(spacing: 12) {
                ForEach(shelf.photos) { photo in
                  VStack {
                    Image(uiImage: photo.thumbnail)
                      .resizable()
                      .scaledToFill()
                      .frame(width: 88, height: 88)
                      .clipShape(RoundedRectangle(cornerRadius: 8))
                    Button("Remove", systemImage: "trash") {
                      store.removePhoto(photo.id)
                    }
                    .font(.caption)
                    .minimumScaledTouchTarget()
                    .accessibilityLabel("Remove photo from \(shelf.name)")
                  }
                }
              }
            }
          }
          Button("Take photo", systemImage: "camera") {
            openCamera()
          }
          .minimumScaledTouchTarget()
          PhotosPicker(selection: $pickerItems, maxSelectionCount: 8, matching: .images) {
            Label("Attach photos", systemImage: "photo.on.rectangle")
          }
          .minimumScaledTouchTarget()
          .onChange(of: pickerItems) { _, items in
            guard !items.isEmpty else { return }
            let selected = items
            pickerItems = []
            Task { await store.attach(selected) }
          }
        }
      }

      Section {
        if let cameraError {
          AccessibleStatusLabel(text: cameraError, kind: .warning)
        }
        if let sealError {
          AccessibleStatusLabel(text: sealError, kind: .error)
        }
        Button("Finish and seal", systemImage: "lock.fill", action: onSeal)
          .buttonStyle(.borderedProminent)
          .disabled(store.photoCount == 0)
          .minimumScaledTouchTarget()
        if store.photoCount == 0 {
          Text("Add at least one shelf photo before sealing.")
            .font(.caption)
            .foregroundStyle(.secondary)
        }
      }
    }
    .accessibilityStatusAnnouncements(status)
    .sheet(isPresented: $showCamera, onDismiss: {
      camera.release(.stillCamera)
    }) {
      ShelfStillCamera { image in
        store.add(image)
      }
    }
  }

  private var status: String {
    let name = store.activeShelf?.name ?? "Shelf"
    return "\(name), \(store.photoCount) shelf photos"
  }

  private var nameBinding: Binding<String> {
    Binding(
      get: { store.activeShelf?.name ?? "" },
      set: { store.renameActive($0) }
    )
  }

  private func openCamera() {
    cameraError = nil
    guard camera.canUseStillCamera, camera.tryAcquire(.stillCamera) else {
      cameraError = camera.statusLine
      return
    }
    guard UIImagePickerController.isSourceTypeAvailable(.camera) else {
      camera.release(.stillCamera)
      cameraError = "This device has no camera. Attach photos instead."
      return
    }
    showCamera = true
  }
}

private struct ShelfStillCamera: UIViewControllerRepresentable {
  let onCapture: (UIImage) -> Void
  @Environment(\.dismiss) private var dismiss

  func makeUIViewController(context: Context) -> UIImagePickerController {
    let picker = UIImagePickerController()
    picker.sourceType = .camera
    picker.delegate = context.coordinator
    picker.view.accessibilityLabel = "Still camera for one shelf photo"
    return picker
  }

  func updateUIViewController(_ controller: UIImagePickerController, context: Context) {}

  func makeCoordinator() -> Coordinator {
    Coordinator(onCapture: onCapture, dismiss: dismiss)
  }

  final class Coordinator: NSObject, UINavigationControllerDelegate, UIImagePickerControllerDelegate {
    let onCapture: (UIImage) -> Void
    let dismiss: DismissAction

    init(onCapture: @escaping (UIImage) -> Void, dismiss: DismissAction) {
      self.onCapture = onCapture
      self.dismiss = dismiss
    }

    func imagePickerController(
      _ picker: UIImagePickerController,
      didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]
    ) {
      if let image = info[.originalImage] as? UIImage {
        onCapture(image)
      }
      dismiss()
    }

    func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
      dismiss()
    }
  }
}
