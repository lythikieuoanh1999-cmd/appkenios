import SwiftUI

struct AdminView: View {
    @EnvironmentObject var store: AppStore
    @State private var users: [AdminUser] = []
    @State private var error: String?
    @State private var pwUser: AdminUser?

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text("Quản trị tài khoản người dùng. Có thể khóa/mở, đổi mật khẩu giúp người dùng, đặt gói.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                ForEach(users) { u in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(u.username).bold()
                            if (u.isAdmin ?? 0) == 1 {
                                Text("admin").font(.caption2).padding(.horizontal, 6).padding(.vertical, 2)
                                    .background(Theme.accent.opacity(0.2)).foregroundStyle(Theme.accent)
                                    .clipShape(Capsule())
                            }
                            if u.plan == "pro" {
                                Text("PRO").font(.caption2).padding(.horizontal, 6).padding(.vertical, 2)
                                    .background(Color.green.opacity(0.2)).foregroundStyle(.green)
                                    .clipShape(Capsule())
                            }
                            Spacer()
                            if (u.banned ?? 0) == 1 {
                                Text("đã khóa").font(.caption).foregroundStyle(.red)
                            }
                        }
                        if let e = u.email, !e.isEmpty { Text(e).font(.caption).foregroundStyle(.secondary) }
                        if let p = u.phone, !p.isEmpty { Text(p).font(.caption).foregroundStyle(.secondary) }
                        HStack {
                            Menu("Thao tác") {
                                Button((u.banned ?? 0) == 1 ? "Mở khóa" : "Khóa tài khoản",
                                       role: (u.banned ?? 0) == 1 ? nil : .destructive) {
                                    Task { await ban(u, !(((u.banned ?? 0) == 1))) }
                                }
                                Button(u.plan == "pro" ? "Đặt về Free" : "Nâng lên Pro") {
                                    Task { await setPlan(u, u.plan == "pro" ? "free" : "pro") }
                                }
                                Button("Đổi mật khẩu giúp") { pwUser = u }
                            }
                            .font(.caption)
                        }
                    }
                    .padding(.vertical, 2)
                }
            }
            .navigationTitle("Quản trị")
            .task { await reload() }
            .refreshable { await reload() }
            .sheet(item: $pwUser) { u in AdminPasswordSheet(user: u) { Task { await reload() } } }
            .alert("Lỗi", isPresented: .constant(error != nil)) {
                Button("OK") { error = nil }
            } message: { Text(error ?? "") }
        }
    }

    private func reload() async {
        do { users = try await store.api.adminUsers() }
        catch { self.error = error.localizedDescription }
    }
    private func ban(_ u: AdminUser, _ banned: Bool) async {
        do { _ = try await store.api.adminBan(u.id, banned: banned); await reload() }
        catch { self.error = error.localizedDescription }
    }
    private func setPlan(_ u: AdminUser, _ plan: String) async {
        do { _ = try await store.api.adminSetPlan(u.id, plan: plan); await reload() }
        catch { self.error = error.localizedDescription }
    }
}

struct AdminPasswordSheet: View {
    @EnvironmentObject var store: AppStore
    @Environment(\.dismiss) var dismiss
    let user: AdminUser
    var onDone: () -> Void
    @State private var newPassword = ""
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("Đổi mật khẩu cho \(user.username)") {
                    SecureField("Mật khẩu mới (≥6 ký tự)", text: $newPassword)
                    Button("Xác nhận") { Task { await save() } }.disabled(newPassword.count < 6)
                }
                if let error { Text(error).foregroundStyle(.red).font(.footnote) }
            }
            .navigationTitle("Đổi mật khẩu")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Đóng") { dismiss() } } }
        }
    }
    private func save() async {
        do {
            _ = try await store.api.adminSetPassword(user.id, newPassword: newPassword)
            onDone(); dismiss()
        } catch { self.error = error.localizedDescription }
    }
}
