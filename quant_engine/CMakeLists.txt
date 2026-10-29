cmake_minimum_required(VERSION 3.20)
project(quantpulse VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(QP_BUILD_PYTHON "Moduł Pythona (pybind11)" ON)
option(QP_BUILD_TESTS "Testy C++ (GoogleTest, pobierany z GitHuba)" OFF)

if(MSVC)
  # /utf-8: polskie znaki w kodzie; /permissive-: zgodność ze standardem
  add_compile_options(/W4 /utf-8 /permissive- /EHsc)
else()
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# Biblioteka nagłówkowa - cały silnik
add_library(quantpulse_core INTERFACE)
target_include_directories(quantpulse_core INTERFACE ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_compile_features(quantpulse_core INTERFACE cxx_std_20)

if(QP_BUILD_PYTHON)
  find_package(Python 3.10 REQUIRED COMPONENTS Interpreter Development.Module)
  find_package(pybind11 3.0 CONFIG REQUIRED)
  pybind11_add_module(_core src/bindings.cpp)
  target_link_libraries(_core PRIVATE quantpulse_core)
  target_compile_definitions(_core PRIVATE QUANTPULSE_VERSION="${PROJECT_VERSION}")
  install(TARGETS _core DESTINATION quantpulse)
endif()

if(QP_BUILD_TESTS)
  include(FetchContent)
  FetchContent_Declare(googletest
    URL https://github.com/google/googletest/archive/refs/tags/v1.15.2.tar.gz
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
  set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)   # Windows: ta sama biblioteka uruchomieniowa
  FetchContent_MakeAvailable(googletest)
  enable_testing()
  add_executable(qp_tests tests/cpp/test_rolling.cpp)
  target_link_libraries(qp_tests PRIVATE quantpulse_core GTest::gtest_main)
  include(GoogleTest)
  gtest_discover_tests(qp_tests)
endif()
