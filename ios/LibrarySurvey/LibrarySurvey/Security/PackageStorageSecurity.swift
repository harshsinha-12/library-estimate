import Foundation

enum PackageStorageSecurity {
  static let protection: FileProtectionType = .completeUntilFirstUserAuthentication

  static func secureDirectory(_ url: URL, fileManager: FileManager = .default) throws {
    try fileManager.setAttributes([.protectionKey: protection], ofItemAtPath: url.path)
    var values = URLResourceValues()
    values.isExcludedFromBackup = true
    var mutableURL = url
    try mutableURL.setResourceValues(values)
  }

  static func secureFile(_ url: URL, fileManager: FileManager = .default) throws {
    try fileManager.setAttributes([.protectionKey: protection], ofItemAtPath: url.path)
    var values = URLResourceValues()
    values.isExcludedFromBackup = true
    var mutableURL = url
    try mutableURL.setResourceValues(values)
  }

  static func secureTree(_ root: URL, fileManager: FileManager = .default) throws {
    try secureDirectory(root, fileManager: fileManager)
    guard let enumerator = fileManager.enumerator(
      at: root,
      includingPropertiesForKeys: [.isDirectoryKey],
      options: [.skipsHiddenFiles]
    ) else { return }
    for case let url as URL in enumerator {
      let isDirectory = try url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory == true
      if isDirectory {
        try secureDirectory(url, fileManager: fileManager)
      } else {
        try secureFile(url, fileManager: fileManager)
      }
    }
  }
}
