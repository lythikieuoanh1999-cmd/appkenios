import SwiftUI

struct RootView: View {
    @EnvironmentObject var store: AppStore

    var body: some View {
        Group {
            if !store.isLoggedIn {
                LoginView()
            } else {
                MainTabView()
            }
        }
        .tint(Theme.accent)
        .preferredColorScheme(store.isDark ? .dark : .light)
    }
}

struct MainTabView: View {
    @EnvironmentObject var store: AppStore

    var body: some View {
        TabView(selection: $store.tab) {
            ChatView()
                .tabItem { Label("Trò chuyện", systemImage: "bubble.left.and.bubble.right.fill") }
                .tag(0)
            LibraryView()
                .tabItem { Label("Thư viện", systemImage: "clock.arrow.circlepath") }
                .tag(1)
            SettingsView()
                .tabItem { Label("Cài đặt", systemImage: "gearshape.fill") }
                .tag(2)
            if store.isAdmin {
                AdminView()
                    .tabItem { Label("Quản trị", systemImage: "person.2.badge.gearshape.fill") }
                    .tag(3)
            }
        }
        .task {
            await store.loadProviders()
            await store.loadKeys()
            await store.refreshConversations()
        }
    }
}
