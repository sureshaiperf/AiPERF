package com.aiperf.product_service.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.Data;

@Data
@Entity
@Table(name = "products")
public class Product {

    @Id
    private Integer id;

    @Column(name = "product_name")
    private String productName;

    private Double price;
}