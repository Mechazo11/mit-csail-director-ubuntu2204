
if(NOT DEFINED VTK_CMAKE_DIR)
  message(SEND_ERROR "VTK_CMAKE_DIR is not defined, cannot load vtkWrapPython.cmake")
endif()

if(NOT VTK_WRAP_PYTHON)
  message(FATAL_ERROR "VTK was built without Python enabled (VTK_WRAP_PYTHON=FALSE).")
endif()

#include(${VTK_CMAKE_DIR}/vtkWrapPython.cmake) # VTK==7.1.1
include(${VTK_CMAKE_DIR}/vtkModuleWrapPython.cmake) # VTK>=8.9
function(wrap_python library_name sources)
  
  vtk_wrap_python3(${library_name}Python generated_python_sources "${sources}")
  
  add_library(${library_name}PythonD ${generated_python_sources})
  add_library(${library_name}Python MODULE ${library_name}PythonInit.cxx)
  
  target_link_libraries(${library_name}PythonD ${library_name})
  foreach(c ${VTK_LIBRARIES})
    target_link_libraries(${library_name}PythonD ${c}PythonD)
  endforeach(c)
  target_link_libraries(${library_name}Python ${library_name}PythonD)
  set_target_properties(${library_name}Python PROPERTIES PREFIX "")
  if(WIN32 AND NOT CYGWIN)
    set_target_properties(${library_name}Python PROPERTIES SUFFIX ".pyd")
  endif(WIN32 AND NOT CYGWIN)

  install(TARGETS ${library_name}Python DESTINATION ${DD_INSTALL_PYTHON_DIR}/director)
  install(TARGETS ${library_name}PythonD DESTINATION ${DD_INSTALL_LIB_DIR})

endfunction()
