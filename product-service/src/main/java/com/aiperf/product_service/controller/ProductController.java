package com.aiperf.product_service.controller;

import com.aiperf.product_service.entity.Product;
import com.aiperf.product_service.repository.ProductRepository;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
public class ProductController {

    private final ProductRepository repository;

    public ProductController(ProductRepository repository) {
        this.repository = repository;
    }

    @GetMapping("/products")
    public List<Product> getProducts() {
        return repository.findAll();
    }

   @GetMapping("/products/{id}")
    public Product getProduct(@PathVariable Integer id) {
        return repository.findById(id).orElse(null);
    }
}