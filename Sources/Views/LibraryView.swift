import SwiftUI
import UniformTypeIdentifiers

struct LibraryView: View {
    @State private var seg = 0
    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                Picker("", selection: $seg) {
                    Text("Lịch sử").tag(0)
                    Text("File").tag(1)
                }
                .pickerStyle(.segmented).padding()
                if seg == 0 { HistoryPane() } else { FilesPane() }
            }
            .navigationTitle("Thư viện")
        }
    }
}

struct HistoryPane: View {
    @EnvironmentObject var store: AppStore
    @State private var search = ""

    private var filtered: [Conversation] {
        guard !search.isEmpty else { return store.conversations }
        return store.conversations.filter { ($0.title ?? "").localizedCaseInsensitiveContains(search) }
    }

    var body: some View {
        List {
            HStack {
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                TextField("Tìm kiếm...", text: $search)
            }
            Button {
                store.openConversation(nil)
            } label: {
                Label("Hội thoại mới", systemImage: "plus").foregroundStyle(Theme.accent)
            }
            ForEach(filtered) { c in
                Button { store.openConversation(c) } label: {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(c.title ?? "Hội thoại").foregroundStyle(.primary).lineLimit(1)
                        if let p = c.provider {
                            HStack(spacing: 5) {
                                Circle().fill(providerColor(p)).frame(width: 7, height: 7)
                                Text(p.capitalized).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            .onDelete(perform: delete)
        }
        .task { await store.refreshConversations() }
        .refreshable { await store.refreshConversations() }
        .overlay {
            if store.conversations.isEmpty {
                Text("Bạn chưa lưu cuộc trò chuyện nào").foregroundStyle(.secondary)
            }
        }
    }

    private func delete(_ offsets: IndexSet) {
        let ids = offsets.map { filtered[$0].id }
        Task {
            for id in ids { _ = try? await store.api.deleteConversation(id) }
            await store.refreshConversations()
        }
    }
}

struct FilesPane: View {
    @EnvironmentObject var store: AppStore
    @State private var category = "all"
    @State private var files: [FileItem] = []
    @State private var showImporter = false
    @State private var error: String?
    @State private var exportDoc: ExportableFile?

    private let cats = [("all", "Tất cả"), ("image", "Ảnh"), ("code", "Code"), ("document", "Tài liệu")]

    var body: some View {
        VStack(spacing: 0) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack {
                    ForEach(cats, id: \.0) { c in
                        Button { category = c.0; Task { await reload() } } label: {
                            Text(c.1).font(.caption)
                                .padding(.horizontal, 12).padding(.vertical, 6)
                                .background(category == c.0 ? Theme.accent : Color(.secondarySystemBackground))
                                .foregroundStyle(category == c.0 ? .white : .primary)
                                .clipShape(Capsule())
                        }
                    }
                }.padding(.horizontal)
            }
            List {
                ForEach(files) { f in
                    HStack {
                        Image(systemName: categoryIcon(f.category)).foregroundStyle(Theme.accent)
                        VStack(alignment: .leading) {
                            Text(f.name).lineLimit(1)
                            Text(humanSize(f.size)).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button { Task { await download(f) } } label: {
                            Image(systemName: "arrow.down.circle")
                        }
                    }
                }
                .onDelete(perform: deleteFiles)

                Button { showImporter = true } label: {
                    VStack {
                        Label("Tải file lên từ máy", systemImage: "plus")
                        Text("Ảnh · PDF · Code · Tài liệu").font(.caption).foregroundStyle(.secondary)
                    }.frame(maxWidth: .infinity)
                }
            }
        }
        .task { await reload() }
        .fileImporter(isPresented: $showImporter,
                      allowedContentTypes: [.item], allowsMultipleSelection: false) { handleImport($0) }
        .fileExporter(isPresented: Binding(get: { exportDoc != nil }, set: { if !$0 { exportDoc = nil } }),
                      document: exportDoc, contentType: .data,
                      defaultFilename: exportDoc?.filename ?? "file") { _ in exportDoc = nil }
        .alert("Lỗi", isPresented: .constant(error != nil)) {
            Button("OK") { error = nil }
        } message: { Text(error ?? "") }
    }

    private func reload() async {
        if let list = try? await store.api.listFiles(category: category) { files = list }
    }

    private func handleImport(_ result: Result<[URL], Error>) {
        guard case .success(let urls) = result, let url = urls.first else { return }
        let access = url.startAccessingSecurityScopedResource()
        defer { if access { url.stopAccessingSecurityScopedResource() } }
        guard let data = try? Data(contentsOf: url) else { return }
        let ext = url.pathExtension.lowercased()
        let cat: String
        if ["png", "jpg", "jpeg", "gif", "heic", "webp"].contains(ext) { cat = "image" }
        else if ["swift", "py", "js", "ts", "java", "c", "cpp", "go", "rs", "rb", "json", "html", "css"].contains(ext) { cat = "code" }
        else if ["pdf", "doc", "docx", "txt", "md", "xls", "xlsx", "ppt", "pptx"].contains(ext) { cat = "document" }
        else { cat = "other" }
        Task {
            do {
                _ = try await store.api.uploadFile(name: url.lastPathComponent, category: cat,
                                                   dataBase64: data.base64EncodedString())
                await reload()
            } catch { self.error = error.localizedDescription }
        }
    }

    private func download(_ f: FileItem) async {
        do {
            let d = try await store.api.downloadFile(f.id)
            guard let data = Data(base64Encoded: d.dataBase64) else { return }
            exportDoc = ExportableFile(data: data, filename: d.name)
        } catch { self.error = error.localizedDescription }
    }

    private func deleteFiles(_ offsets: IndexSet) {
        let ids = offsets.map { files[$0].id }
        Task {
            for id in ids { _ = try? await store.api.deleteFile(id) }
            await reload()
        }
    }
}

struct ExportableFile: FileDocument {
    static var readableContentTypes: [UTType] { [.data] }
    var data: Data
    var filename: String
    init(data: Data, filename: String) { self.data = data; self.filename = filename }
    init(configuration: ReadConfiguration) throws {
        data = configuration.file.regularFileContents ?? Data(); filename = "file"
    }
    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper {
        FileWrapper(regularFileWithContents: data)
    }
}
