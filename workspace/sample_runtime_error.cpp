#include <iostream>

int main() {
    const int n = 8;
    int* data = new int[n];
    for (int i = 0; i <= n; ++i) {
        data[i] = i * 2;
    }
    long long total = 0;
    for (int i = 0; i < n; ++i) {
        total += data[i];}
    std::cout << "Total: " << total << std::endl;
    delete[] data;
    return 0;
}
