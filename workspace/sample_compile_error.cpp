#include <iostream>
#include <vector>

int main() {
    std::vector<int> numbers = {1, 2, 3, 4, 5};
    int total = 0;
    for (int n : numbers) {
        total += n;
    }
    std::cout << "Sum: " << total << std::endl;
    return 0;
}
