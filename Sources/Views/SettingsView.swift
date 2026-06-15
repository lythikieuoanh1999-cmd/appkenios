import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var store: AppStore
    @State private var email = ""
    @State private var phone = ""
    @State private var newPassword = ""
    @State private var message: String?
    @State private var connected: Bool?
    @State private var keyProvider: Provider?
    @State private var showConnections = false

    var body: some View {
        NavigationStack {
            Form {
                // API KEYS
                Section {
                    ForEach(store.providers) { p in
                        Button { keyProvider = p } label: {
                            HStack {
                                Circle().fill(providerColor(p.id)).frame(width: 10, height: 10)
                                Text(p.label.components(separatedBy: " · ").first ?? p.id)
                                    .foregroundStyle(.primary)
                                if p.free {
                                    Text("Free").font(.caption2)
                                        .padding(.horizontal, 6).padding(.vertical, 2)
                                        .background(Color.green.opacity(0.2)).foregroundStyle(.green)
                                        .clipShape(Capsule())
                                }
                                Spacer()
                                if store.configuredKeys.contains(p.id) {
                                    Image(systemName: "checkmark").foregroundStyle(.green)
                                } else {
                                    Text("Chưa có").font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                } header: {
                    Text("API Keys")
                } footer: {
                    Text("Key được mã hóa (Fernet) khi lưu trên máy chủ. AI có key sẽ hiện ra để chọn khi chat.")
                }

                // SERVER
                Section("Kết nối máy chủ (\(store.serverType))") {
                    LabeledContent("URL / IP", value: store.baseURL)
                    HStack {
                        Text("Trạng thái")
                        Spacer()
                        if let connected {
                            Circle().fill(connected ? .green : .red).frame(width: 8, height: 8)
                            Text(connected ? "Đang kết nối" : "Mất kết nối")
                                .foregroundStyle(connected ? .green : .red)
                        } else { ProgressView() }
                    }
                    Button("Quản lý máy chủ (VPS / Hosting)") { showConnections = true }
                }

                // ACCOUNT
                Section("Tài khoản") {
                    LabeledContent("Tên đăng nhập", value: store.username ?? "-")
                    TextField("Gmail", text: $email)
                        .textInputAutocapitalization(.never).keyboardType(.emailAddress)
                    TextField("Số điện thoại", text: $phone).keyboardType(.phonePad)
                    SecureField("Đổi mật khẩu (để trống nếu không đổi)", text: $newPassword)
                    Button("Lưu thay đổi") { Task { await saveProfile() } }
                }

                // OTHER
                Section("Khác") {
                    Picker("Ngôn ngữ", selection: Binding(
                        get: { store.language },
                        set: { store.setLanguage($0) })) {
                        Text("Tiếng Việt").tag("vi")
                        Text("English").tag("en")
                    }
                    Toggle("Giao diện tối", isOn: Binding(
                        get: { store.isDark }, set: { store.setDark($0) }))
                }

                if let message { Text(message).foregroundStyle(.green).font(.footnote) }

                Section {
                    Button("Đăng xuất", role: .destructive) { store.logout() }
                }
            }
            .navigationTitle("Cài đặt")
            .sheet(item: $keyProvider) { p in KeyEntryView(provider: p) }
            .sheet(isPresented: $showConnections) { ConnectionsView() }
            .task {
                await store.loadKeys()
                connected = (try? await store.api.getConfig()) != nil
            }
            .onAppear { email = store.email ?? ""; phone = store.phone ?? "" }
        }
    }

    private func saveProfile() async {
        do {
            _ = try await store.api.updateProfile(email: email, phone: phone,
                                                  newPassword: newPassword.isEmpty ? nil : newPassword)
            store.updateLocalUser(email: email, phone: phone)
            newPassword = ""; message = "Đã cập nhật."
        } catch { message = error.localizedDescription }
    }
}

struct KeyEntryView: View {
    @EnvironmentObject var store: AppStore
    @Environment(\.dismiss) var dismiss
    let provider: Provider
    @State private var key = ""
    @State private var message: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack {
                        Circle().fill(providerColor(provider.id)).frame(width: 10, height: 10)
                        Text(provider.label).bold()
                        if provider.free {
                            Text("Free").font(.caption2).padding(.horizontal, 6).padding(.vertical, 2)
                                .background(Color.green.opacity(0.2)).foregroundStyle(.green).clipShape(Capsule())
                        }
                    }
                    SecureField("Dán API key tại đây", text: $key)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    Text("Model mặc định: \(provider.defaultModel)")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section {
                    Button("Lưu key") { Task { await save() } }
                        .disabled(key.isEmpty)
                    if store.configuredKeys.contains(provider.id) {
                        Button("Xóa key", role: .destructive) { Task { await remove() } }
                    }
                }
                if let message { Text(message).font(.footnote).foregroundStyle(.secondary) }
            }
            .navigationTitle("Nhập API Key")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Đóng") { dismiss() } } }
        }
    }

    private func save() async {
        do { _ = try await store.api.saveKey(provider: provider.id, apiKey: key)
            await store.loadKeys(); dismiss()
        } catch { message = error.localizedDescription }
    }
    private func remove() async {
        do { _ = try await store.api.deleteKey(provider: provider.id)
            await store.loadKeys(); dismiss()
        } catch { message = error.localizedDescription }
    }
}
